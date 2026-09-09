# Telegram-native formatting

Bot render phần trả lời chính bằng Telegram HTML có kiểm soát để message dễ đọc hơn trong group mà không phụ thuộc Markdown raw.

## 1. Format được hỗ trợ

Sanitizer cho phép các tag Telegram sau:

- `<b>`, `<i>`, `<u>`, `<s>`;
- `<tg-spoiler>`;
- `<code>`, `<pre>`;
- `<blockquote>`, `<blockquote expandable>`;
- `<a href="https://...">` với URL HTTP/HTTPS hợp lệ.

Các alias phổ biến như `<strong>`, `<em>`, `<ins>`, `<strike>`, `<del>` được normalize về tag Telegram tương ứng.

## 2. Legacy Markdown normalization

Trước khi sanitize, pipeline chuyển một số Markdown model thường leak sang HTML an toàn:

- `# heading` → bold;
- `- item` / `* item` → bullet `•`;
- `**bold**` → `<b>`;
- inline code backtick → `<code>`;
- fenced code block → `<pre>`.

Existing `<pre>`/`<code>` được bảo vệ trong lúc normalize để không bị xử lý lồng sai.

Unsupported tag bị loại, text được escape, link không phải HTTP/HTTPS bị bỏ tag link.

## 3. Safe splitting

`split_telegram_html()` sanitize trước rồi chia theo tối đa **3900 UTF-16 code units** của visible text.

Splitter:

- ưu tiên cắt ở paragraph/newline/câu khi gần giới hạn;
- không cắt surrogate pair UTF-16;
- đóng active tag ở cuối chunk;
- mở lại tag tương ứng ở chunk kế tiếp;
- giữ `<b>`, `<pre>`, `<blockquote>`, `<a>` và các tag hỗ trợ ở trạng thái cân bằng.

Giới hạn 3900 thấp hơn hard limit Telegram để chừa biên an toàn cho markup/entity handling.

## 4. Khi nào dùng `parse_mode="HTML"`

Sau khi split, handler so sánh payload đã sanitize với plain text tương ứng.

- Nếu chunk thực tế không cần markup, bot gửi **plain text** và không set `parse_mode`.
- Nếu chunk có markup Telegram thực sự, bot gửi với `parse_mode="HTML"` và tắt link preview.

Điều này tránh đưa plain text qua HTML parser không cần thiết.

Nếu Telegram trả lỗi parse/entity cho một chunk HTML, riêng chunk đó được retry dưới dạng plain text. Các lỗi `400` khác không được retry mù để tránh che lỗi hoặc gửi trùng.

Với multipart answer, part đầu dùng `message.reply`; các part sau dùng `bot.send_message`. Nếu request nằm trong Telegram forum topic, `message_thread_id` được giữ cho các part tiếp theo.

## 5. Memory và forum topic

Conversation memory lưu **plain text** của assistant response, không lưu HTML markup.

Telegram forum topic được cô lập bằng conversation key `(chat_id, message_thread_id)`, nên formatting/multipart delivery của một topic không làm history của topic khác bị lẫn.

## 6. Source footer và image delivery

Source footer vẫn dùng formatter HTML riêng sau main answer. Image results được gửi ở bước tiếp theo; lỗi một image/source delivery không thay đổi nội dung main answer đã gửi thành công.

## 7. Verification

Offline verification:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests
```

Regression cần bảo vệ các invariant sau:

- sanitizer chỉ giữ tag/link hợp lệ;
- legacy Markdown được normalize mà không phá code block;
- split theo UTF-16 không làm hỏng tag hoặc surrogate pair;
- plain payload không ép `parse_mode="HTML"`;
- HTML parse/entity error mới được retry plain text;
- multipart delivery giữ `message_thread_id` trong forum topic;
- ChatMemory lưu plain text thay vì markup.
