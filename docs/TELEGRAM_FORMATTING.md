# Telegram-native formatting

Bot render phần trả lời chính bằng Telegram HTML có kiểm soát để message dễ đọc hơn trong group.

Các format được hỗ trợ: `<b>`, `<i>`, `<u>`, `<s>`, `<tg-spoiler>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>` và link `<a href="https://...">`.

Trước khi gửi, output AI đi qua sanitizer để cân bằng tag, escape text, loại tag không hỗ trợ, chặn link không phải HTTP/HTTPS và tương thích với một số Markdown thường bị model leak như `**bold**`, heading, bullet và fenced code.

Message dài được chia theo tối đa 3900 UTF-16 code units của phần text hiển thị. Splitter đóng tag ở cuối chunk và mở lại ở chunk kế tiếp nên không cắt hỏng `<b>`, `<pre>`, `<blockquote>` hoặc `<a>`.

Handler gửi answer với `parse_mode="HTML"` và tắt link preview. Chỉ khi Telegram trả lỗi parse/entity thì chunk đó mới được retry dưới dạng plain text; lỗi 400 khác không được retry mù để tránh che lỗi hoặc gửi trùng.

Conversation memory lưu bản plain text của assistant response, không lưu HTML markup. Footer nguồn vẫn dùng formatter HTML riêng hiện có.
