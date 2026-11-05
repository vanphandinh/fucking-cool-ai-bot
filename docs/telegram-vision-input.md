# Telegram vision input

Vision routing dùng cùng generic provider framework với text. Production order hiện tại là **Chainnode primary -> OpenRouter fallback**; image handling không hard-code provider identity. Router chọn configured vision providers theo `VISION_PROVIDER_ORDER`, `ProviderCapabilities`, health state và image count.

## Input được hỗ trợ

`TelegramMediaLoader` có thể nhìn media trên message hiện tại và message được reply.

Application limit hiện tại:

```env
MAX_IMAGES_PER_REQUEST=1
```

Cả Chainnode và OpenRouter vision slots hiện advertise:

```text
supports_vision=True
max_images=1
```

Effective image limit là:

```text
min(MAX_IMAGES_PER_REQUEST, max capability của configured vision providers)
```

Với production registry hiện tại, effective limit là một ảnh/request.

Media hợp lệ:

- Telegram photo -> JPEG;
- document `image/jpeg`;
- document `image/png`;
- document `image/webp`.

Image MIME khác bị từ chối. Plain image không có trigger text/caption không tự gọi bot.

## Multi-image behavior

Request vượt application limit bị loader chặn trước model call. Nếu request vượt capability của các configured vision candidates, router không chuyển nó sang text model và không giả định provider khác support được input.

Current production flow:

```text
Telegram media
  -> MAX_IMAGES_PER_REQUEST=1
  -> generic capability-aware vision routing
  -> Chainnode / route=vision / max_images=1
  -> OpenRouter / route=vision / max_images=1 (fallback nếu configured + qualified)
```

UX khi vượt limit được sinh từ effective capability, không phải chuỗi provider-specific hard-code trong handler.

Nếu sau này provider vision có `max_images=4`, router vẫn chỉ nhận tối đa một ảnh nếu `MAX_IMAGES_PER_REQUEST=1`. Muốn dùng 4 ảnh cần cả application limit và provider capability đều cho phép.

## Memory / byte safety

- `MAX_IMAGE_BYTES`: max từng ảnh;
- `MAX_TOTAL_IMAGE_BYTES`: max tổng payload ảnh;
- unknown Telegram `file_size` vẫn dùng bounded `BytesIO` để chặn trước khi buffer vượt limit.

Raw image bytes chỉ tồn tại trong request RAM:

```text
Telegram bytes
 -> TelegramMediaLoader
 -> UserRequest.images
 -> build_user_content()
 -> data:image/<mime>;base64,...
 -> selected vision provider request
```

`ChatMemory` không lưu raw bytes/base64; sau answer chỉ lưu marker text `[kèm N ảnh] ...`.

Provider error excerpt redact image data URL trước log/status.

## Capability routing

Generic route selection:

```text
VISION_PROVIDER_ORDER
 -> registered family slots
 -> route=vision?
 -> supports_vision?
 -> max_images >= request image_count?
 -> healthy?
 -> ordered attempt/fallback
```

Current config:

```env
VISION_ENABLED=1
VISION_PROVIDER_ORDER=chainnode,openrouter
AI_PROVIDERS__CHAINNODE__VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
AI_PROVIDERS__OPENROUTER__VISION_MODELS=
MAX_IMAGES_PER_REQUEST=1
```

Chainnode và OpenRouter đều dùng stable provider key riêng; text/vision slots được phân biệt bằng `ProviderCapabilities.route`, không bằng UI name riêng.

`VISION_ENABLED=0` tắt vision nhưng không tắt text.

## Video input (mention-only)

Bot đọc video (`video`/`animation`/`video_note`, kể cả từ tin nhắn forward) **chỉ khi được tag/mention** — filter `TriggeredMessage` giữ nguyên, không có xử lý tự động. Mỗi video thành đúng 1 frame JPEG giữa video qua `ffmpeg` (`app/bot/video_frames.py`), fallback thumbnail Telegram khi thiếu ffmpeg/lỗi. Giới hạn: `VISION_VIDEO_ENABLED=1`, `MAX_VIDEO_BYTES=15728640` (< 20MB hạn download Bot API hosted), `MAX_VIDEO_DURATION_SEC=180`, `VIDEO_FFMPEG_TIMEOUT_SEC=15`. Nguồn media: tin hiện tại + `reply_to_message` + `external_reply` (chỉ media, không quoted). Story forward không có `file_id` nên Bot API không lấy được.

Nếu không có configured/capable vision provider, user nhận generic `NoCapableProvider` UX. Image request không fallback sang text-only slot.

OpenRouter vision chỉ nên được bật sau khi candidate vượt live `/v1/models` hard gates và image/tool qualification trong `scripts/probe_openrouter.py`; model ID không hard-code trong runtime. Khi `AI_PROVIDERS__OPENROUTER__PROVIDER` được đặt, vision request cũng mang provider allowlist/order đó.

## Tools và provider fallback trong vision request

Vision completion có cùng generic tools như text:

- `web_search(query)`;
- `image_search(query)`;
- `fetch_url(url, mode?)`.

Tool budget là request-wide:

- hard cap 8 tool calls;
- tối đa 2 tool calls chạy song song;
- `MAX_TOOL_ROUNDS` discovery rounds.

Provider transition không reset budget. Provider-specific continuation metadata chỉ replay trong cùng provider. Nếu budget đã đóng, later vision provider chỉ nhận generic Fresh Synthesis context với tools disabled. Completed tool output không chạy lại chỉ vì route chuyển từ Chainnode sang OpenRouter.

## Verification

```bash
python -m unittest tests.test_vision -v
python -m unittest tests.test_chainnode_provider -v
python -m unittest tests.test_openrouter_provider -v
python -m unittest tests.test_openrouter_probe -v
python -m unittest tests.test_provider_registry -v
python -m unittest tests.test_provider_portability -v
python -m unittest tests.test_fresh_synthesis_routing -v
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
```

Production smoke nên gồm:

1. một screenshot/UI image;
2. một ảnh thường;
3. reply một ảnh;
4. image + web search;
5. image + direct URL;
6. request vượt effective image limit để xác nhận bị chặn trước model call;
7. `/status` để xác nhận configured vision providers/order/cooldown;
8. controlled Chainnode vision failure -> OpenRouter vision fallback sau khi OpenRouter đã live-qualified;
9. restore Chainnode ngay sau fallback smoke.
