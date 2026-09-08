# Telegram vision input

Tài liệu này mô tả **đúng flow vision hiện tại trong source**. Vision là route riêng; text request dùng
free-tier-first text provider pool và không bị chuyển sang vision slot.

## 1. Input được hỗ trợ

`TelegramMediaLoader` kiểm tra hai vị trí:

1. media trên message hiện tại (`source="current"`);
2. media trên message được reply (`source="reply"`).

Vì vậy flow Telegram hiện tại cung cấp tối đa **2 ảnh thực tế** cho một request. Default
`MAX_IMAGES_PER_REQUEST=3` là capability/config ceiling của provider; nó không triển khai album aggregation
hay tự gom 3 Telegram message.

Media hợp lệ:

- Telegram photo → `image/jpeg`;
- document `image/jpeg`;
- document `image/png`;
- document `image/webp`.

Image document có MIME `image/*` khác sẽ bị từ chối. Document không phải image được bỏ qua như text-only
media.

### Trigger UX

Các flow chính:

- photo/document + caption có `@bot`;
- reply một ảnh + `@bot <câu hỏi>`;
- reply một ảnh + `/ask <câu hỏi>`;
- reply trực tiếp tin của bot rồi nhập câu hỏi (theo trigger reply-to-bot hiện có).

Plain image không có text/caption trigger sẽ không tự gọi bot.

## 2. Giới hạn bytes và memory safety

Các giới hạn:

- `MAX_IMAGE_BYTES` — tối đa cho từng ảnh;
- `MAX_TOTAL_IMAGE_BYTES` — tối đa cho tổng ảnh của request;
- `MAX_IMAGES_PER_REQUEST` — trần số ảnh/capability.

Nếu Telegram cung cấp `file_size`, loader reject ảnh vượt giới hạn trước khi download. Nếu `file_size` không
có, download destination là bounded `BytesIO`: lần `write()` vượt `MAX_IMAGE_BYTES` bị chặn **trước khi bytes
được ghi thêm vào buffer**. Sau download vẫn có length check defense-in-depth.

Sau khi lấy current/reply image, loader kiểm tra tổng bytes trước khi tạo `UserRequest`.

## 3. Data lifecycle

Raw image bytes chỉ nằm trong `ImageAttachment`/`UserRequest` trong RAM.

```text
Telegram bytes
   -> TelegramMediaLoader
   -> UserRequest.images
   -> build_user_content()
   -> data:image/<mime>;base64,...
   -> provider request
```

`ChatMemory` không lưu image bytes/base64. Sau answer, memory chỉ lưu text marker:

```text
[kèm N ảnh] <câu hỏi>
```

Provider response/error excerpts đi qua image-data redaction trước khi có thể xuất hiện trong error string,
log hoặc `/status`; payload `data:image/...;base64,...` được thay bằng placeholder.

## 4. Capability-aware routing

Router giữ cả text và vision provider trong một danh sách nhưng lọc bằng `ProviderCapabilities` trước khi
thử provider.

### Text route

Default free-tier-first:

```text
Groq GPT-OSS 120B
  -> Cloudflare GLM-4.7-Flash
  -> OpenRouter Free Router
  -> Gemini 3.8 Flash
```

Text request yêu cầu `route="text"`, nên không chạy vào vision slot.

### Vision route

| Slot config | Điều kiện tạo | Provider name runtime |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` + `GEMINI_VISION_MODEL` | `gemini_vision` |
| `groq_qwen38` | `GROQ_API_KEY` + model thứ 1 trong `GROQ_VISION_MODELS` | `groq_qwen38` |
| `groq_qwen36` | `GROQ_API_KEY` + model thứ 2 trong `GROQ_VISION_MODELS` | `groq_qwen36` |
| `cloudflare` | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` + model | `cloudflare` |

Default source hiện tại:

```env
VISION_ENABLED=1
GEMINI_VISION_MODEL=gemini-3.8-flash
GROQ_VISION_MODELS=qwen/qwen3.8-27b,qwen/qwen3.6-27b
CLOUDFLARE_VISION_MODEL=@cf/google/gemma-4-26b-a4b-it
VISION_PROVIDER_ORDER=groq_qwen38,cloudflare,groq_qwen36,gemini
MAX_IMAGES_PER_REQUEST=3
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

Cloudflare được đặt giữa hai Groq vision slot để tăng provider diversity trước khi thử Groq model thứ hai.
`VISION_ENABLED=0` không thay đổi text pool. Image request khi không còn capable vision provider trả UX
“chưa có model đọc ảnh được cấu hình”.

## 5. Tools, metadata, quota và fallback

Vision completion dùng cùng **ba tool** với text completion:

- `web_search(query)` — tìm thông tin web khi cần dữ liệu ngoài conversation;
- `image_search(query)` — tìm ảnh Internet khi user yêu cầu ảnh/ảnh tham khảo;
- `fetch_url(url, mode?)` — đọc một URL cụ thể; `mode=x_thread` chỉ dùng cho X/Twitter status URL.

System prompt yêu cầu tách điều nhìn thấy trong ảnh khỏi dữ liệu tìm trên web. Nếu user đã đưa một URL cụ thể
cần đọc, AI ưu tiên `fetch_url` trước `web_search`. Với X/Twitter status, `fetch_url` dùng direct resolver
FxTwitter → X oEmbed → generic reader khi `X_FETCH_ENABLED=1`, nên không cần search lại raw X URL qua SearXNG.

Chi tiết direct-X: [X_CONTENT_FETCHING.md](X_CONTENT_FETCHING.md).

Các model mới có thể trả metadata bắt buộc phải round-trip qua tool turn. Adapter giữ metadata đó trong
**cùng provider** rồi loại bỏ trước khi chuyển transcript sang provider khác:

- Gemini 3.x: giữ `tool_calls[].extra_content.google.thought_signature`;
- OpenRouter/reasoning model: giữ assistant-level `reasoning_details`, `reasoning` hoặc `reasoning_content`;
- khi fallback sang provider khác, các field provider-specific bị strip để tránh provider kế từ chối unknown field.

Free-tier-first defaults dùng `MAX_TOOL_ROUNDS=2` và `MAX_CONTEXT_TURNS=6` để hạn chế số request/token. Router
vẫn có safety cap nội bộ tối đa 8 tool calls cho một completion và dùng chung budget qua retry/fallback.

**Free-tier-first không đồng nghĩa code bảo đảm chi phí $0.** Bot không biết billing plan của credential. Nếu
Groq, Cloudflare hoặc Gemini project/account ở paid tier thì provider có thể tính phí theo policy của họ.

Provider health/fallback:

- `401/403` → disable slot đến process restart;
- `429` → cooldown theo numeric `Retry-After`, nếu không parse được thì 60 giây;
- network/`5xx` transient → sau 2 lỗi liên tiếp cooldown 30 giây;
- success → reset transient state/cooldown.

## 6. Telegram answer formatting

Phần trả lời chính được render bằng Telegram-native HTML có kiểm soát, không phải Markdown raw. Output đi qua
sanitizer/splitter trước khi gửi; ChatMemory lưu plain text. Chi tiết:
[TELEGRAM_FORMATTING.md](TELEGRAM_FORMATTING.md).

## 7. Verification

Offline verification:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests
```

Regression coverage gồm:

- effective text/vision provider order và free-tier-first defaults (`tests/test_free_routing.py`);
- Gemini thought-signature replay và cross-provider metadata isolation (`tests/test_free_routing.py`);
- OpenRouter reasoning metadata replay (`tests/test_provider_metadata.py`);
- capability routing và multimodal payload;
- Telegram media size enforcement khi `file_size` không biết trước;
- redaction image data URL khỏi provider errors;
- image search normalization/delivery (`tests/test_image_search.py`);
- direct URL/X tool policy (`tests/test_search_policy_prompt.py`, `tests/test_url_tool_integration.py`).

Trước production rollout vẫn nên smoke-test bằng credential thật với ít nhất: screenshot OCR, UI screenshot,
ảnh thường, reply-image, image + web-search request, và image + direct URL request. CI không thực hiện live
provider/X E2E.