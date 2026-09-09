# Telegram vision input

Vision routing dùng cùng generic provider framework với text. **Current production registration là B.AI**, nhưng image handling không hard-code kiến trúc theo B.AI: router chọn configured vision providers theo `VISION_PROVIDER_ORDER`, `ProviderCapabilities`, health state và image count.

## Input được hỗ trợ

`TelegramMediaLoader` có thể nhìn media trên message hiện tại và message được reply.

Application limit hiện tại:

```env
MAX_IMAGES_PER_REQUEST=1
```

Current B.AI vision slot cũng advertise:

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

- Telegram photo → JPEG;
- document `image/jpeg`;
- document `image/png`;
- document `image/webp`.

Image MIME khác bị từ chối. Plain image không có trigger text/caption không tự gọi bot.

## Multi-image behavior

Request vượt application limit bị loader chặn trước model call. Nếu request vượt capability của các configured vision candidates, router không chuyển nó sang text model và không giả định một provider khác support được input.

Current production flow:

```text
Telegram media
  -> MAX_IMAGES_PER_REQUEST=1
  -> generic capability-aware vision routing
  -> bai / route=vision / max_images=1
```

UX khi vượt limit được sinh từ effective capability, không phải chuỗi B.AI-specific hard-code trong handler.

Sau này nếu thêm provider vision có `max_images=4`, router vẫn chỉ nhận tối đa một ảnh nếu `MAX_IMAGES_PER_REQUEST=1`. Muốn dùng 4 ảnh cần cả application limit và provider capability đều cho phép.

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
VISION_PROVIDER_ORDER=bai
BAI_VISION_MODEL=qwen3.8-flash
MAX_IMAGES_PER_REQUEST=1
```

Current B.AI family slots dùng cùng stable key `bai`; text/vision được phân biệt bằng `ProviderCapabilities.route`, không bằng UI name riêng.

`VISION_ENABLED=0` tắt vision nhưng không tắt text.

Nếu không có configured/capable vision provider, user nhận generic `NoCapableProvider` UX. Image request không fallback sang text-only slot.

## Tools và provider fallback trong vision request

Vision completion có cùng generic tools như text:

- `web_search(query)`;
- `image_search(query)`;
- `fetch_url(url, mode?)`.

Tool budget là request-wide:

- hard cap 8 tool calls;
- tối đa 2 tool calls chạy song song;
- `MAX_TOOL_ROUNDS` discovery rounds.

Provider transition không reset budget. Provider-specific continuation metadata chỉ replay trong cùng provider. Nếu budget đã đóng, một later vision provider chỉ nhận generic Fresh Synthesis context với tools disabled.

Current registry chỉ có B.AI, nên chưa có second vision provider để fallback trong production. Framework đã giữ sẵn ordered fallback semantics cho provider mới.

## Verification

```bash
python -m unittest tests.test_vision -v
python -m unittest tests.test_provider_registry -v
python -m unittest tests.test_provider_portability -v
python -m unittest tests.test_bai_provider -v
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
7. `/status` để xác nhận configured vision providers/order/cooldown.
