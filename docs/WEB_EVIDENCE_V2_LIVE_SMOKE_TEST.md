# Web Evidence V2 — bộ 21 câu hỏi smoke test với model thật

Kết quả chạy model thật (2026-10-16) và verdict phát hành nằm ở
[RELEASE_DECISION.md](acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md)
và [QUALITY_EVALUATION.md](acceptance/web_evidence_v2_independent_validation_20261016/QUALITY_EVALUATION.md).
V2 hiện **chưa đạt điều kiện pilot**: bản kiểm định 2026-10-16 ghi Q6 sai 3/3
lượt và Q15 claim phụ sai; hai lượt P2 ngày 2026-10-18 vẫn sai Q6 0/2 và Q15
1/2 (chi tiết ở `docs/acceptance/web_evidence_v2_live_validation_20261018/EVALUATION.md`,
mục 8).

**Mục đích:** chạy bộ 21 câu trên pipeline V2 duy nhất, có provider và mạng thật. Tài liệu
này là **bộ test + hướng dẫn**, không phải kết quả. Các lần chạy cũ (2026-10-14/16) là hồ
sơ lịch sử; chất lượng model thật sau cutover vẫn là **NOT MEASURED** — các con số benchmark
trong repo là proxy offline, không thay thế được bước này.

> Không giả lập kết quả. Nếu chưa có provider/mạng, cứ để nguyên trạng thái
> `PENDING LIVE TEST` và chạy khi môi trường sẵn sàng. Mỗi smoke run gọi API model
> thật và có thể tiêu quota; phải xin xác nhận tường minh ngay trước từng run.
> Không gọi API chỉ dựa trên việc kế hoạch đã được duyệt.

## 1. Chuẩn bị

1. Cấu hình provider trong `.env` (không commit secret).
2. Xóa ba key đã retire khỏi `.env`, chạy `python scripts/sync_env.py`, rồi redeploy bot.
   ```env
   WEB_EVIDENCE_MAX_EXTRACTED_CHARS=120000
   WEB_AUTO_FETCH_MAX_SOURCES=2
   WEB_AUTO_FETCH_BUDGET_SEC=20
   ```
3. Giữ nguyên model, nhiệt độ, `SEARCH_BACKEND` và timeout trong suốt lượt chạy.

Khi chạy smoke trên host, SearXNG trong compose không publish port. Dùng override
loopback để harness dùng đúng backend có ngày đăng thay vì rơi xuống DDGS:
```sh
docker compose -f docker-compose.yml -f docker-compose.smoke.yml \n  --profile searxng up -d searxng
SEARXNG_URL=http://127.0.0.1:8080 python -m scripts.web_evidence_live_smoke \n  --output docs/acceptance/<run-dir>/run_1.json
```
Harness chạy preflight một truy vấn trước khi gọi model; nếu cả hai backend lỗi,
nó ghi artifact với `search_preflight` và thoát mã 2, không tiêu quota model.

Chạy 21 câu **hai lượt độc lập** trên V2, giữ nguyên provider/model, nhiệt độ,
`SEARCH_BACKEND` và timeout; lưu thành hai artifact riêng và không ghi đè kết quả
trước. Mỗi lượt ghi câu trả lời, nguồn/dẫn chứng, trace truy vấn search (query, URL,
ngày xuất bản nếu có), thời gian phản hồi và lỗi timeout/runtime. Trace không chứa
snippet, response body hay credential. Không còn lượt V1 để so sánh; hai lượt V2
không chứng minh non-inferiority so với V1.

**Authorization gate:** ngay trước khi gọi provider cho từng lượt, hỏi người dùng
xác nhận rõ. Nếu chưa xác nhận, không chạy lệnh smoke và giữ `PENDING LIVE TEST`.

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
    image search không đổi).
21. Đọc một URL X/Twitter status cụ thể bằng `mode=x_thread` và tóm tắt nội dung
    (kiểm tra X resolver không đổi).

## 3. Cách chấm

Mỗi câu chấm 4 tiêu chí, thang 0–1, kèm ghi chú ngắn:

| Tiêu chí | Đạt khi |
| --- | --- |
| Đúng nội dung | Trả lời khớp với trang nguồn |
| Dẫn chứng có thật sự hỗ trợ | Trích dẫn/URL nguồn thực sự chứa dữ liệu được nêu |
| Không bịa | Không có số liệu/khẳng định không xuất hiện trong nguồn |
| Xử lý thiếu bằng chứng | Nhóm E: nói rõ không đủ dữ liệu thay vì bịa |

Ghi riêng: timeout, lỗi runtime, thời gian phản hồi (giây) mỗi câu.

## 4. Ngưỡng GO đề xuất (dự án cá nhân)

- Mỗi một trong hai lượt đạt ít nhất **18/21** câu V2.
- Câu 6 trả lời đúng ở cả hai lượt (kiểm chứng chính thức Artemis; không lặp lại plan cũ).
- **Không** có lỗi bảo mật, citation ngoài evidence hoặc prompt injection nghiêm trọng (đặc biệt nhóm B/E), và không có claim sai trọng yếu/không được nguồn hỗ trợ.
- **Không** có lỗi hồi quy quan trọng (timeout/runtime tăng bất thường).
- Toàn bộ test tự động vẫn pass.
- Đây là cổng thực nghiệm vận hành, không phải kiểm định thống kê; không tuyên bố V2 không-thua-kém V1 vì V1 không còn chạy được.

Đây là ngưỡng vận hành thực dụng, không phải kết luận thống kê về chất lượng model.

## 5. Vận hành và rollback

Sau cutover, quan sát 48–72 giờ (độ chính xác, trích dẫn sai, timeout, lỗi đọc trang, mức
dùng tài nguyên). Không có cờ để tắt V2; rollback duy nhất là redeploy image/commit trước.

## 6. Điều kiện hoàn thành

Chỉ đánh dấu “V2 ổn định sau cutover” khi cả hai bộ 21 câu đạt ngưỡng GO ở mục 4, đã được chấm tay theo rubric và test tự động pass. Kết quả `web_evidence_pilot_audit.py` exit 0 chỉ cho biết artifact không gặp stop condition có thể kiểm tra máy; nó không phải quality pass. Nếu thiếu xác nhận, provider, mạng, hoặc human grading, giữ trạng thái **hoàn thiện offline, chờ kiểm tra thực tế** (`PENDING LIVE TEST`) và không tuyên bố chất lượng production.
