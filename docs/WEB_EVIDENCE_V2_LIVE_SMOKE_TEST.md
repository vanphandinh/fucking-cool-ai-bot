# Web Evidence V2 — bộ 21 câu hỏi smoke test với model thật

**Mục đích:** so sánh V1 và V2 trên câu hỏi thực tế, có provider và mạng. Tài liệu này
là **bộ test + hướng dẫn**, không phải kết quả. Phiên tạo tài liệu (2026-10-14) chạy
**offline, không có provider** nên chất lượng model thật vẫn là **NOT MEASURED** — các
con số benchmark trong repo là proxy offline, không thay thế được bước này.

> Không giả lập kết quả. Nếu chưa có provider/mạng, cứ để nguyên trạng thái
> `PENDING LIVE TEST` và chạy khi môi trường sẵn sàng.

## 1. Chuẩn bị

1. Cấu hình provider trong `.env` (không commit secret).
2. Chạy **V1** trước: để mặc định an toàn, khởi động lại bot theo cách triển khai hiện tại.
   ```env
   WEB_EVIDENCE_V2_ENABLED=false
   WEB_AUTO_FETCH_ENABLED=false
   ```
3. Chạy **V2** sau: bật cả hai flag rồi khởi động lại bot (giữ nguyên model, nhiệt độ,
   `SEARCH_BACKEND`, timeout giữa hai lượt để so sánh tương đương).
   ```env
   WEB_EVIDENCE_V2_ENABLED=true
   WEB_AUTO_FETCH_ENABLED=true
   ```

Chạy cùng 21 câu cho cả hai cấu hình, ghi lại: câu trả lời, nguồn/dẫn chứng, thời gian
phản hồi, lỗi timeout/runtime. Đổi thứ tự V1/V2 giữa các câu nếu muốn giảm thiên lệch.

## 2. Bộ 21 câu hỏi

Nhóm A — thông tin đơn giản (4):

1. Thủ đô của New Zealand là gì, dân số khoảng bao nhiêu?
2. Nước sôi ở bao nhiêu độ C tại áp suất mực nước biển, và ở đỉnh Everest thì sao?
3. Ngôn ngữ Python ra mắt năm nào và do ai tạo ra?
4. Hành tinh nào có ngày dài nhất trong Hệ Mặt Trời (tính theo giờ Trái Đất)?

Nhóm B — thông tin nằm sâu trong trang (4) — hỏi một chi tiết nằm sâu trong bài, không
phải ở mở đầu:

5. Theo bài Wikipedia “Great Barrier Reef”, rạn san hô trải dài khoảng bao nhiêu km dọc
   bờ biển Queensland?
6. Theo trang chính thức của NASA về chương trình Artemis, lần hạ cánh có phi hành đoàn
   dự kiến thuộc sứ mệnh Artemis số mấy?
7. Trong tiêu chuẩn ISO 8601, định dạng ngày “cơ bản” (basic format) viết thế nào so với
   định dạng “mở rộng” (extended)?
8. Theo bài Wikipedia về “Mount Everest”, độ cao chính thức mới nhất được công bố năm nào
   và là bao nhiêu mét?

Nhóm C — tổng hợp nhiều nguồn (4):

9. So sánh tốc độ tải tối đa lý thuyết của USB 3.2 Gen 2, Thunderbolt 4 và HDMI 2.1.
10. Tổng hợp các mốc thời gian chính của chuẩn Wi-Fi (802.11n/ac/ax/be) và năm ra mắt.
11. So sánh định mức tiêu thụ điện của một máy tính để bàn phổ thông và một laptop phổ
    thông khi chạy tác vụ nhẹ.
12. Liệt kê các khác biệt chính về hiệu năng giữa CPU và GPU cho tác vụ AI suy luận.

Nhóm D — thông tin mới, có yếu tố thời gian (3):

13. Phiên bản ổn định mới nhất của Python tính đến hôm nay là gì và phát hành khi nào?
14. Hiện tại ai là CEO của Microsoft?
15. Sự kiện công nghệ lớn nào diễn ra trong tháng này (tháng hiện tại của bạn)?

Nhóm E — thiếu bằng chứng hoặc nguồn mâu thuẫn (3) — kỳ vọng model nói rõ không đủ dữ
liệu, không bịa:

16. Giá chính xác của một chiếc iPhone mới nhất tại Việt Nam hôm nay là bao nhiêu?
17. Dân số chính xác của một xã miền núi nhỏ bất kỳ vào đêm nay là bao nhiêu?
18. Có đúng là nước biển sẽ dâng 5 mét trong 5 năm tới không?

Nhóm F — URL trực tiếp, image search và X resolver (3):

19. Đọc trực tiếp URL https://example.com và cho biết trang nói gì (kiểm tra đường
    `fetch_url` trực tiếp; URL cố định để tái lập).
20. Dùng image search cho truy vấn “Eiffel Tower” và mô tả 1–2 kết quả (kiểm tra đường
    image search giữ nguyên hành vi V1).
21. Đọc một URL X/Twitter status cụ thể bằng `mode=x_thread` và tóm tắt nội dung
    (kiểm tra X resolver giữ nguyên hành vi V1).

## 3. Cách chấm

Mỗi câu chấm 4 tiêu chí, thang 0–1, kèm ghi chú ngắn:

| Tiêu chí | Đạt khi |
| --- | --- |
| Đúng nội dung | Trả lời khớp với trang nguồn |
| Dẫn chứng có thật sự hỗ trợ | Trích dẫn/URL nguồn thực sự chứa dữ liệu được nêu |
| Không bịa | Không có số liệu/khẳng định không xuất hiện trong nguồn |
| Xử lý thiếu bằng chứng | Nhóm E: nói rõ không đủ dữ liệu thay vì bịa |

Ghi riêng: timeout, lỗi runtime, thời gian phản hồi (giây) mỗi câu cho V1 và V2.

## 4. Ngưỡng GO đề xuất (dự án cá nhân)

- Ít nhất **18/21** câu V2 đạt yêu cầu.
- Số câu đúng của V2 **không thấp hơn** V1.
- **Không** có lỗi bảo mật hoặc prompt injection nghiêm trọng (đặc biệt nhóm B/E).
- **Không** có lỗi hồi quy quan trọng (timeout/runtime tăng bất thường).
- Toàn bộ test tự động vẫn pass.

Đây là ngưỡng vận hành thực dụng, không phải kết luận thống kê về chất lượng model.

## 5. Bật/tắt và rollback

Bật V2: đặt cả hai flag `true` rồi khởi động lại bot; quan sát 48–72 giờ (độ chính xác,
trích dẫn sai, timeout, lỗi đọc trang, mức dùng tài nguyên).

Rollback: đặt cả hai flag `false` rồi khởi động lại bot. Cache V1/V2 tách namespace
(`:v2`) nên không lẫn kết quả.

## 6. Điều kiện hoàn thành

Chỉ đánh dấu “sẵn sàng dùng V2” khi bộ 21 câu đạt ngưỡng GO ở mục 4 **và** test tự động
pass. Nếu thiếu provider/mạng, giữ trạng thái **hoàn thiện offline, chờ kiểm tra thực tế**
(`PENDING LIVE TEST`) và không tuyên bố chất lượng production.
