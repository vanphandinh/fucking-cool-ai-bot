# Web Evidence V2 — QUALITY EVALUATION (chấm độc lập 2026-10-16)

Chấm trên 74 câu trả lời thật (37 × A/B + 3 câu C thử nghiệm). Chuẩn mối mâu thuẫn (Artemis/eu/prices) xác minh bằng nguồn web độc lập (research Web): NASA news-release 27/2/2026 + 3/3/2026 (Artemis IV = lần hạ cánh có người đầu tiên; Artemis III → LEO 2027; Artemis II đã bay 1–10/4/2026), bảng giá iPhone 18/Duo 41,99/64,99 triệu (Apple Newsroom + TGDĐ + FPT Shop + Tuổi Trẻ, 9/2026), OpenAI DevDay 29/9/2026 + GPT-6 Astra 3/9/2026.

## 1. Tính điểm bộ 21 (chuẩn)

| Q | Chế độ A (V1) | Chế độ B (V2) |
| --- | --- | --- |
| 1–5, 7–14 | PASS (nội dung đúng, không bịa nghiêm trọng) | PASS (V2 chi tiết hơn; Q13 khẳng định phụ 3.14.3/2-2026 không kiểm trực tiếp được — UNVERIFIABLE, không mâu thuẫn) |
| Q6 | PASS (Artemis IV + II đã bay 4/2026 + III ghép nối LEO) | **FAIL — CONTRADICTED**: nhầm Artemis III là lần hạ cánh 2028 (lặp 3/3 lượt: bare B, rerun, config C). Root: ở tầng retrieval, hit "news-release nasa-adds-mission-…-architecture" (27/2/2026) không vào evidence top-8 lần này; trang kế hoạch cũ nổi top và model tin snippet cũ. |
| Q15 | PASS (GITEX VN, iTECH, SEMICONNECT, WSW) | **FAIL — UNSUPPORTED/MIS-STATEMENT**: "DevDay 4/10, ra mắt GPT-6 Astra tại DevDay" — sự thật là DevDay 29/9, Astra phát hành 3/9, "ra mắt tại DevDay" không có nguồn. Nguồn yếu (blog VN tổng hợp) vào evidence. Biến động: 1/3 lượt (rerun) không nêu lỗi này. |
| Q16 | **FAIL — STALE** ("iPhone 17" vẫn là "mới nhất", không có dòng 18/Duo đã niêm yết) | PASS (iPhone 18 Pro Max 41,99tr, Duo 64,99tr — khớp chuẩn độc lập) |
| Q17, Q18 | PASS (nói rõ không đủ dữ liệu; phủ định 5m/5 năm) | PASS (tương đương; V2 chi tiết khớp chuẩn hơn) |
| Q19–21 | PASS (example.com; 2 ảnh Eiffel; tweet đầu tiên 21/3/2006) | PASS |

**Bộ 21: V1 = 20/21 (lỗi Q16 — freshness), V2 = 19/21 (lỗi Q6 — mâu thuẫn + Q15 — claim phụ sai).**

## 2. Bộ extended 16

| ID | A (V1) | B (V2) |
| --- | --- | --- |
| 101,102,105,106,107,109,113,114,110 | PASS | PASS |
| 103,104,116 | PASS (Artemis chuẩn; 116 có claim phụ "lùi 2029" không kiểm chứng được — minor) | PASS (103/110/116 đều đúng chuẩn; 104 nói "giữa năm 2026" thay cho 27/2–3/3/2026 — date lag nhỏ) |
| 108 | PASS (111,2mm từ 1993; 8–9cm/2000–2025) | PASS (NOAA; nói rõ đã cố tìm số NASA trực tiếp mà không thấy — minh bạch) |
| 111 | PASS (404 §15.5.5; 429 = RFC 6585) | PASS (404 §15.5.5 đúng nguyên văn; 429 RFC 6585 mục 4, có link) |
| 115 | PASS (trích định nghĩa đúng, công khai cảnh báo truncation) | PASS (đúng §15.5.5: không nói vĩnh viễn/tạm thời; 410; cacheable) |
| 112 | PASS nội dung, nhưng **ảnh thật chua xem bằng mắt → UNVERIFIED** | tương tự UNVERIFIED (thêm claim "không lẫn icon 'towergit'" — vô nghĩa文字) |

**Extended: V1 = 16/16 (2 residual minor), V2 = 16/16 (2 residual minor).**

## 3. Tổng hợp 37 câu



- Bộ 21 chuẩn: V1 20/21 (lỗi Q16 stale), V2 19/21 (lỗi Q6 CONTRADICTED + Q15 claim phụ sai).

- Bộ extended 16: V1 16/16, V2 16/16.

- **Tổng 37 câu: V1 = 36/37; V2 = 35/37.** V2 thua V1 1 câu trên điểm tổng;

  điểm dừng tại 2 lỗi V2 (Q6 sai ổn định 3/3 lượt, Q15 claim phụ sai 1/3 lượt)

  đối với 1 lỗi V1 (Q16 bỏ sót dòng iPhone mới nhất).



Điều kiện D4: "≥18/21" đạt cho cả hai. Điều kiện "V2 không thấp hơn V1" **KHÔNG đạt nghiêm ngặt**

(19 < 20 trên bộ chuẩn; 35 < 36 tổng). Zero critical unsupported claims: **KHÔNG đạt tuyệt đối**

(Q15 V2; Q116 V1 minor).## 4. Claim grounding tổng thể

- Invented citations (URL không nằm trong tập guard cho phép): **0/74** (1 false-positive audit do regex cắt URL có dấu `)` — đã sửa tool và xác minh guard `allowed` đầy đủ).
- Guard: 0 removal, 0 error, 74 lượt. Metric ≥95% citation support: **đạt (100% không invented; mệnh đề/claim-level grounding điểm thêm ở mục 1–3).**
- V1 không có guard (đúng như thiết kế; `citation_diagnostics` = None ở mọi hàng V1).
