# Telegram vision input

Tài liệu này mô tả flow vision hiện tại trong source. Vision là route riêng; text request không bị chuyển sang vision slot.

## 1. Input được hỗ trợ

`TelegramMediaLoader` kiểm tra hai vị trí:

1. media trên message hiện tại (`source="current"`);
2. media trên message được reply (`source="reply"`).

Vì vậy flow Telegram hiện tại cung cấp tối đa **2 ảnh thực tế** cho một request. Default `MAX_IMAGES_PER_REQUEST=3` là capability/config ceiling, không triển khai album aggregation hay tự gom ba Telegram message.

Media hợp lệ:

- Telegram photo → `image/jpeg`;
- document `image/jpeg`;
- document `image/png`;
- document `image/webp`.

Image document có MIME `image/*` khác sẽ bị từ chối. Document không phải image được bỏ qua như text-only media.

### Trigger UX

- photo/document + caption có `@bot`;
- reply một ảnh + `@bot <câu hỏi>`;
- reply một ảnh + `/ask <câu hỏi>`;
- reply trực tiếp tin của bot rồi nhập câu hỏi.

Plain image không có text/caption trigger sẽ không tự gọi bot.

## 2. Giới hạn bytes và memory safety

- `MAX_IMAGE_BYTES` — tối đa cho từng ảnh;
- `MAX_TOTAL_IMAGE_BYTES` — tối đa tổng ảnh/request;
- `MAX_IMAGES_PER_REQUEST` — ceiling capability chung.

Nếu Telegram cung cấp `file_size`, loader reject ảnh vượt giới hạn trước download. Nếu không có `file_size`, download đi vào bounded `BytesIO`; lần `write()` vượt `MAX_IMAGE_BYTES` bị chặn trước khi bytes được ghi thêm. Sau download vẫn có length check defense-in-depth.

Raw image bytes chỉ tồn tại trong `ImageAttachment`/`UserRequest` trong RAM:

```text
Telegram bytes
   -> TelegramMediaLoader
   -> UserRequest.images
   -> build_user_content()
   -> data:image/<mime>;base64,...
   -> provider request
```

`ChatMemory` không lưu image bytes/base64. Sau answer, memory chỉ lưu marker:

```text
[kèm N ảnh] <câu hỏi>
```

Provider error excerpt được redact image data URL trước khi có thể xuất hiện trong log hoặc `/status`.

## 3. Capability-aware routing

Router giữ text và vision slots trong một danh sách nhưng lọc bằng `ProviderCapabilities` trước khi thử provider.

### Text route

Default:

```text
B.AI qwen3.8-flash
  -> Gemini 3.8 Flash
  -> Groq GPT-OSS 120B
  -> Cloudflare GLM-4.7-Flash
  -> OpenRouter Free Router
```

Text request yêu cầu route text, nên không chạy vào vision slot.

### Vision route

| Slot config | Điều kiện tạo | Runtime name | Max images |
|---|---|---|---:|
| `bai` | `BAI_API_KEY` + supported `BAI_VISION_MODEL` + slot có trong order | `bai_vision` | **1** |
| `gemini` | `GEMINI_API_KEY` + `GEMINI_VISION_MODEL` | `gemini_vision` | `MAX_IMAGES_PER_REQUEST` |
| `groq_qwen38` | `GROQ_API_KEY` + model thứ 1 trong `GROQ_VISION_MODELS` | `groq_qwen38` | 3 |
| `cloudflare` | account ID + token + `CLOUDFLARE_VISION_MODEL` | `cloudflare` | `MAX_IMAGES_PER_REQUEST` |
| `groq_qwen36` | `GROQ_API_KEY` + model thứ 2 trong `GROQ_VISION_MODELS` | `groq_qwen36` | 3 |

Default hiện tại:

```env
VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
GEMINI_VISION_MODEL=gemini-3.8-flash
GROQ_VISION_MODELS=qwen/qwen3.8-27b,qwen/qwen3.6-27b
CLOUDFLARE_VISION_MODEL=@cf/google/gemma-4-26b-a4b-it
VISION_PROVIDER_ORDER=bai,gemini,groq_qwen38,cloudflare,groq_qwen36
MAX_IMAGES_PER_REQUEST=3
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

Điểm quan trọng: B.AI vision hiện rollout-capped ở **1 ảnh/request** dù config ceiling chung là 3. Request có 2 ảnh sẽ tự skip B.AI vision vì không đủ capability rồi thử Gemini/Groq/Cloudflare theo order. Không được nâng B.AI `max_images` chỉ vì `MAX_IMAGES_PER_REQUEST=3`; cần live multi-image compatibility test trước.

`VISION_ENABLED=0` không thay đổi text pool. Image request khi không còn capable vision provider trả UX “chưa có model đọc ảnh được cấu hình”.

Chi tiết B.AI: [BAI_INTEGRATION.md](BAI_INTEGRATION.md).

## 4. Tools, metadata, quota và fallback

Vision completion dùng cùng ba tool với text:

- `web_search(query)`;
- `image_search(query)`;
- `fetch_url(url, mode?)`.

Nếu user đã cung cấp URL cụ thể, AI ưu tiên `fetch_url` trước `web_search`. Với X/Twitter status, direct resolver là FxTwitter → X oEmbed → generic reader khi `X_FETCH_ENABLED=1`.

Các model có thể trả metadata cần round-trip trong cùng provider. Runtime giữ metadata đó qua tool continuation rồi loại provider-specific fields trước cross-provider fallback:

- Gemini: `tool_calls[].extra_content.google.thought_signature`;
- reasoning-compatible providers: `reasoning_details`, `reasoning`, `reasoning_content`.

Defaults dùng `MAX_TOOL_ROUNDS=2`; router có safety cap nội bộ tối đa 8 tool calls/completion, dùng chung qua fallback.

Provider health:

- `401/403` → disable slot đến process restart;
- `429` → cooldown theo numeric `Retry-After`, mặc định 60 giây nếu không parse được;
- network/`5xx` transient → sau 2 lỗi liên tiếp cooldown 30 giây;
- success → reset transient state.

Fallback count của router là request-local `ContextVar`, vì vậy concurrent requests không ghi đè metric của nhau.

## 5. Forum topic isolation

Telegram forum topic dùng conversation key `(chat_id, message_thread_id)`. History và per-conversation lock của topic này không được chia sẻ sang topic khác trong cùng supergroup.

Điều này áp dụng cho cả text và image requests: một topic đang xử lý ảnh chậm không chặn topic khác chỉ vì cùng `chat_id`.

## 6. Telegram answer formatting

Answer được sanitize và split bằng Telegram-native HTML pipeline. Plain payload được gửi plain text; chỉ chunk thực sự có markup mới dùng `parse_mode="HTML"`. Nếu Telegram báo parse/entity error cho HTML, chunk đó được retry plain text.

ChatMemory lưu assistant response dưới dạng plain text.

Chi tiết: [TELEGRAM_FORMATTING.md](TELEGRAM_FORMATTING.md).

## 7. Verification

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests
```

Regression coverage quan trọng:

- `tests/test_free_routing.py` — default text/vision order và metadata isolation;
- `tests/test_bai_provider.py` — B.AI capability/model contract;
- `tests/test_router_concurrency.py` — request-local fallback count;
- `tests/test_forum_topic_isolation.py` — history/lock isolation giữa forum topics;
- media size enforcement, multimodal payload và image-data redaction;
- `tests/test_image_search.py` — image search normalization/delivery;
- `tests/test_search_policy_prompt.py`, `tests/test_url_tool_integration.py` — direct URL/X policy.

Trước production rollout vẫn nên smoke-test bằng credential thật với ít nhất: screenshot OCR, UI screenshot, ảnh thường, reply-image, một ảnh qua B.AI, request hai ảnh để xác nhận B.AI được skip đúng capability, image + web-search và image + direct URL.
