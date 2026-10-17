# Web Evidence V2 — RELEASE DECISION (kiểm định độc lập, 2026-10-16)

> **Hồ sơ lịch sử, trước cutover V2-only.** Các cờ `WEB_EVIDENCE_V2_ENABLED`,
> `WEB_EVIDENCE_V2_GROUP_IDS` và `WEB_AUTO_FETCH_ENABLED` đã bị xóa khỏi runtime;
> quyết định dưới đây ghi lại trạng thái và điều kiện tại thời điểm kiểm định,
> không còn là hướng dẫn vận hành hiện tại. Runbook hiện tại:
> [VPS_REDEPLOY_AND_FEATURE_FLAGS.md](../../VPS_REDEPLOY_AND_FEATURE_FLAGS.md).

## 1. Bảng tiêu chí

| # | Tiêu chí | Trạng thái | Bằng chứng |
| --- | --- | --- | --- |
| 1 | Full regression | **PASS (OFFLINE VERIFIED)** | 827 tests OK (skipped=11) sau FIX guard, exit 0; bộ focused 87/65/37/4 đều exit 0; full final chạy nền (số cuối và log nằm trong Git history) |
| 2 | Citation guard safety (bypass) | **PASS (OFFLINE VERIFIED)** | Probe 60/60 (30 doc + 30 mới); **1 bypass HIGH thực sự phát hiện (nested/label-in-anchor) → đã sửa có regression tests**; 0 guard crash trên 74 lượt live |
| 3 | Unicode / fail-closed | **PASS (OFFLINE VERIFIED)** | Surrogate, error path được probe + suite xác nhận |
| 4 | Search cap ≤ 8 | **PASS (OFFLINE VERIFIED)** | `test_web_search_limit_clamp` xanh; không nới cap trong phiên |
| 5 | Image search unaffected | **PASS (OFFLINE VERIFIED)** | `test_image_search` xanh; Q20/XQ112 live đạt (chưa xem ảnh thật bằng mắt — UNVERIFIED) |
| 6 | V1 rollback | **PASS (OFFLINE VERIFIED)** | `citation_diagnostics=None` trên 37 hàng V1; answer không bị guard đụng; suite hardening xanh |
| 7 | Offline evidence recall | **PASS (OFFLINE VERIFIED)** | V1 0,4790 → V2 0,6891; fixture SHA khớp; `network_requests=0` |
| 8 | Real-model answer accuracy | **PARTIAL (REAL MODEL VERIFIED, KHÔNG ĐẠT NGƯỠNG NGHIÊM NGẶT)** | Bộ 21 chuẩn: V1 20/21, V2 19/21 (Q6 V2 CONTRADICTED 3/3 lượt, Q15 claim phụ sai 1 lượt); extended 16: V2 16/16 = V1 16/16; tổng 74 lượt: V1 36/37, V2 35/37 → **V2 KHÔNG chứng minh không thấp hơn V1 trên bộ chuẩn** |
| 9 | Human-scored citation precision (invented URLs) | **PASS (REAL MODEL VERIFIED)** | 0/74 URL ngoài tập evidence (audit từ guard_allowed_urls); guard removed=0, error=0 |
| 10 | Unsupported claim rate | **NOT MET (REAL MODEL VERIFIED)** | ≥1 claim phụ sai (Q15 V2; Q116 V1 minor "lùi 2029"); chưa ZERO như mục tiêu |
| 11 | Production latency | **NOT MEASURED** | Chỉ có latency pilot-scale nhỏ (median 9,8/11,4s; p95 15,8/44,0s) |
| 12 | Token usage | **NOT MEASURED** | adapter không trả usage; benchmark ước tính +16,7% median (≤130%) |
| 13 | GitNexus risk review | **DONE — risks công bố** | `_enforce` CRITICAL (đã sửa kèm tests); `_freshness_bonus` HIGH (không sửa); `detect-changes` CRITICAL trên diff chưa commit |
| 14 | Artifact integrity | **PASS (OFFLINE VERIFIED)** | 8 file + probes/artifacts JSON trong thư mục này, đều exit-0-linked; JSON parse OK |
| 15 | Production rollout | **NOT VERIFIED** | Không deploy; chỉ runbook |

## 2. Verdict

**PASS (offline only, pilot-chờ-bằng-cứng).** Cụ thể hai nhánh決定:

1. **READY FOR MERGE — FLAGS OFF: ĐỒNG Ý (offline verified).**
   - Toàn bộ offline gates xanh; bypass citation HIGH đã đóng; V1/image/cap/bounds nguyên vẹn; benchmark không hồi quy.
   - Điều kiện: merge giữ `WEB_EVIDENCE_V2_ENABLED=false`, `WEB_AUTO_FETCH_ENABLED=false`.

2. **READY FOR V2 PILOT: CHƯA (INSUFFICIENT EVIDENCE cho cờ B/C).**
   - Ngưỡng riêng của dự án cho pilot: (a) ≥18/21 chuẩn — V2 đạt 19/21 ✓; (b) **V2 không thấp hơn V1 — KHÔNG chứng minh được (19 < 20 trên bộ chuẩn, tổng 35 vs 36; Q6 V2 sai ổn định 3/3 lượt trên mọi cấu hình A/B/C)**; (c) zero critical unsupported claims — **không đạt (Q15 V2)**.
   - Lớp lỗi còn mở: freshness-recall — khi query chỉ nhắc "trang chính thức" và SearXNG trả nhiều trang cũ cùng domain, hit news-release mới không lọt top-8 → model dựa snippet cũ. Auto-Fetch (C) KHÔNG mitigates câu này (đã thử nghiệm).
   - Hành động bắt buộc trước pilot: cải thiện tầng retrieval/đa-dạng truy vấn cho nhóm câu "theo trang chính thức của X" (không gán cứng theo URL); chạy lại ma trận bộ 21 ≥2 lượt và chứng minh V2 ≥ V1 trên 2/2 lượt.

## 3. Những rủi ro còn lại

- Q6-style stale-plan failure có thể xảy ra với bất kỳ chủ đề lạc trôi nhanh (lịch trình sự kiện, cầu thủ, phiên bản phần mềm).
- SearXNG brave/google-cse suspended thường trực làm giảm đa dạng nguồn (DDGS lỗi lặp) — cùng một backend interrupted có thể gặp ở production.
- TELEGRAM RENDER UNCONFIRMED cho schemeless/protocol-relative/zero-width/WHATWG-tab (chưa xác nhận trên client thật).
- Token usage thật chưa đo; latency production chưa đo.
- Index GitNexus là của working tree; CRITICAL detect-changes phản ánh volume diff chưa commit — merge handler cần review的人工 không dựa entry exit-code 0.
