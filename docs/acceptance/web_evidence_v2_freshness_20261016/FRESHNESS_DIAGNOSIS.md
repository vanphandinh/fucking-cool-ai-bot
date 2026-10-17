# Web Evidence V2 — FRESHNESS DIAGNOSIS (Gate G1, 2026-10-16)

Bằng chứng cho Phase 1 của plan pilot (tài liệu plan và các probe/artifact tái lập đã được dọn khỏi working tree; bản đã commit lấy lại bằng `git show <sha>:<path>`). **Không có file production nào bị sửa trong phase này.**

## 1. Phép đo

| Phép đo | Lệnh | Artifact |
| --- | --- | --- |
| Tất định (offline) | `probes/freshness_slot_probe.py --mode synthetic` (probe đã dọn) | `freshness_synthetic.json` (đã dọn) |
| Pool thật (SearXNG nội bộ qua mạng compose) | `docker run --rm --network fucking-cool-ai-bot_default --env-file .env -v "$PWD":/app:ro … --mode live` (artifact đã dọn) | `freshness_live.json` (đã dọn) |

Câu hỏi live: Q6 (Artemis, nhóm B), Q13 (Python mới nhất, nhóm D), Q16 (giá iPhone, nhóm E) — lấy từ `docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md`.

## 2. Kết quả tất định — **H3 xác nhận**

Pool dựng theo đúng hình dạng Q6 (nhiều trang lưu trữ cùng domain chính thức chiếm slot, một trang recent hạng trung, một bản release chính thức mới nhất có overlap thấp hơn). Điểm số lấy từ `lexical_score` thật:

```
code=H3_recency_slot_skipped
  IN  score=1.000 lex=1.000 bonus=0.00 age=900.0 recent_ok=False  nasa.gov/artemis-archive-0..5   (7 trang)
  IN  score=0.727 lex=0.667 bonus=0.06 age=300.0 recent_ok=True   space.com/artemis-crew-landing-overview
  out score=0.469 lex=0.319 bonus=0.15 age=30.0  recent_ok=True   nasa.gov/news-release/nasa-adds-mission-to-artemis-architecture
  IN  score=0.164 lex=0.014 bonus=0.15 age=10.0  recent_ok=False  example.com/weather-today
  eligible_out_of_top=['…/nasa-adds-mission-to-artemis-architecture']
```

Cơ chế: `rank_web_results` bỏ qua **toàn bộ** vùng dự phòng recency nếu **bất kỳ** record recent nào đã nằm trong `chosen` (`app/search/source_ranking.py:186-199`). Ở đây trang space.com (recent, hạng trung) đã chiếm slot, nên bản release chính thức mới nhất — record **duy nhất** còn lại thoả `lexical >= 0.30` và `age <= 365` — không được đưa vào top-8. Tệ hơn: một trang recent **lạc đề** (`example.com`, lex 0.014) vẫn nằm trong top-8 nhờ lượt breadth-fill, trong khi bản official mới bị loại.

Kết luận: đây là defect logic thật, tái lập tất định, độc lập với backend.

## 3. Kết quả pool thật — **H1 xác nhận một phần, và một nguyên nhân mới**
Ba lượt live, 24 candidate, **tất cả `age_days=None`**: không result nào mang `published_at`, nên `_freshness_bonus` = 0 và danh sách `recent` **rỗng ở cả ba lượt** ⇒ cơ chế recency không bao giờ chạy trong môi trường này.

Pool Q6 (top-8) là các trang tổng hợp tiếng Việt (dantri, baoquocte, tuoitre, vnexpress, qdnd, baotintuc, 2 post Facebook) — **không có hit nào thuộc nasa.gov**. SearXNG báo `brave: Suspended: too many requests`.

## 4. Phán quyết giả thuyết

| Mã | Phán quyết | Bằng chứng |
| --- | --- | --- |
| **H1** bản release không có trong pool | **XÁC NHẬN (một phần)** | Q6 live: 8/8 kết quả là báo tổng hợp, không có nasa.gov; engine `brave` bị suspended. Ranker không thể chọn thứ backend không trả về. |
| **H2** có trong pool nhưng `lexical < 0.30` | **KHÔNG XÁC NHẬN ở dạng "ngưỡng quá cao"** | Pool live có candidate `lexical` 0.13–0.48 nhưng không candidate nào có `published_at`, nên không thể phân biệt bằng dữ liệu này. Pool tất định cho thấy một bản official thật (lex 0.319) **vượt** ngưỡng mà vẫn bị loại ⇒ vấn đề nằm ở slot, không ở ngưỡng. |
| **H3** guard recency slot chặn swap | **XÁC NHẬN (tất định)** | Bảng mục 2: record `recent_ok=True` duy nhất còn lại bị loại vì một record recent khác đã nằm trong `chosen`. |
| **H4** hit đã vào top-8, lỗi ở tầng sau | **BỊ LOẠI** | Cả pool tất định lẫn pool live đều cho thấy hit không vào top-8; lỗi nằm ở retrieval/ranking, không phải packing. |
| **H5 (mới)** thiếu metadata ngày ⇒ cơ chế recency vô hiệu | **XÁC NHẬN** | 24/24 candidate live có `age_days=None`. `app/search/searxng_backend.py:60` **có** map `publishedDate`, nhưng engine trả về date (brave/google-cse) đang bị suspended — nên thực tế danh sách `recent` luôn rỗng. |

## 5. Hệ quả cho Phase 2 (điều chỉnh so với plan)

- **2.1 (H3) giữ nguyên**: sửa guard slot — defect đã được chứng minh, và đây là thứ duy nhất trong ranker chủ động bỏ qua một candidate hợp lệ.
- **2.2 (H2 — hạ ngưỡng 0.30) BỎ**: không có bằng chứng ủng hộ. Hạ ngưỡng chỉ làm tăng nguy cơ kéo trang recent **lạc đề** vào top — pool tất định đã cho thấy một trang như vậy (`example.com`, lex 0.014) lọt top-8.
- **2.3 (truy vấn phụ có năm hiện tại) nâng lên ưu tiên 2**: vì H5 làm mọi cơ chế dựa vào ngày vô hiệu trên backend hiện tại, đổi truy vấn là cách duy nhất còn lại để tìm bản mới. Giới hạn cứng: tối đa **1** lần/câu, chỉ khi `verification_context`, không nâng cap ≤ 8, không thêm domain cứng.
- **H1 ngoài phạm vi ranker**: pool thiếu nguồn chính thức (brave suspended) chỉ xử lý được ở tầng engine/đa dạng nguồn. Ghi thành hạng mục riêng, **không** hardcode domain vào ranker (vi phạm nguyên tắc 2 của plan).

## 6. Gate G1

**ĐẠT**: H3 xác nhận và H4 bị loại (đủ 2 chiều), kèm 2 artifact JSON tái lập được; thêm mã mới H5 xác nhận. Không file production nào bị sửa trong phase này.
