# Telegram vision input

Vision runtime hiện là **B.AI-only**. Text request và image request vẫn là hai capability route riêng.

## Input được hỗ trợ

`TelegramMediaLoader` có thể nhìn media trên message hiện tại và message được reply, nhưng production config hiện khóa:

```env
MAX_IMAGES_PER_REQUEST=1
```

Đây là giới hạn phù hợp với B.AI vision capability hiện tại (`max_images=1`). Vì vậy một request chỉ được gửi tối đa một ảnh vào model.

Media hợp lệ:

- Telegram photo → JPEG;
- document `image/jpeg`;
- document `image/png`;
- document `image/webp`.

Image MIME khác bị từ chối. Plain image không có trigger text/caption không tự gọi bot.

## Multi-image behavior

Không còn provider vision khác để fallback.

Request có hơn một ảnh:

```text
Telegram media
  -> loader thấy vượt MAX_IMAGES_PER_REQUEST=1
  -> reject trước model call
  -> UX: B.AI hiện chỉ hỗ trợ 1 ảnh mỗi yêu cầu
```

Không gửi hai ảnh sang B.AI, không chuyển sang B.AI text model và không tăng limit chỉ để giữ behavior của kiến trúc multi-provider cũ.

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
 -> B.AI request
```

`ChatMemory` không lưu raw bytes/base64; sau answer chỉ lưu marker text `[kèm N ảnh] ...`.

Provider error excerpt redact image data URL trước log/status.

## Capability routing

Text:

```text
bai / BAI_TEXT_MODEL
```

Vision:

```text
bai_vision / BAI_VISION_MODEL / max_images=1
```

`VISION_ENABLED=0` tắt vision nhưng không tắt text.

Thiếu B.AI key/vision model khi vision bật sẽ không tạo valid vision slot và user nhận B.AI-specific configuration error.

## Tools trong vision request

Vision completion vẫn có cùng tools như text:

- `web_search(query)`;
- `image_search(query)`;
- `fetch_url(url, mode?)`.

Tool budget, fresh synthesis và B.AI health behavior dùng chung router implementation với text.

## Verification

```bash
python -m unittest tests.test_vision -v
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
6. request hai ảnh để xác nhận bị chặn trước model call.
