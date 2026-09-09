# Search Resilience

Search web và image dùng một policy thống nhất với `SEARCH_BACKEND=auto`:

```text
fresh cache
    |
    v
SearXNG (primary, max 1 attempt/request)
    |
    | fail / web result quá ít
    v
DDGS (fallback, max 1 attempt/request)
    |
    | fail
    v
stale cache
    |
    | miss
    v
SearchError
```

## Invariants

- Không ping-pong `SearXNG -> DDGS -> SearXNG` trong cùng request.
- Mỗi backend tối đa một attempt trong một request.
- Backend lỗi được circuit breaker đưa ra khỏi rotation tạm thời và tự probe lại ở request tương lai.
- `SEARCH_BACKEND=searxng` và `SEARCH_BACKEND=ddgs` là strict mode, không fallback ngầm.
- Kết quả partial đã có không bị vứt nếu fallback tiếp theo lỗi.
- Error/timeout/empty response không được cache.
- `CancelledError` được propagate, không bị biến thành backend failure.

## Timeout budget

Các timeout search độc lập với timeout AI provider:

```env
SEARXNG_TIMEOUT_SEC=7.0
DDGS_TIMEOUT_SEC=8.0
SEARCH_TOTAL_TIMEOUT_SEC=15.0
```

Router tạo một deadline tổng. Trước mỗi backend, effective timeout là giá trị nhỏ hơn giữa timeout backend và budget còn lại.

## Circuit breaker

Mặc định:

```env
SEARCH_CIRCUIT_FAILURE_THRESHOLD=3
SEARCH_CIRCUIT_COOLDOWN_SEC=30.0
SEARCH_RATE_LIMIT_COOLDOWN_SEC=180.0
```

State machine:

```text
CLOSED --repeated failure--> OPEN --cooldown--> HALF_OPEN
  ^                                           |
  |---------------- success -----------------|
                       failure -> OPEN
```

Chỉ một request được làm HALF_OPEN probe. Web và image có circuit riêng; DDGS và SearXNG cũng có circuit riêng.

Rate limit/429 mở circuit ngay với cooldown dài hơn. Riêng 429 của một engine bên trong SearXNG không làm application circuit của toàn SearXNG mở nếu SearXNG vẫn trả usable results.

## Partial top-up

- Web: SearXNG trả 2+ usable results thì return ngay. Nếu chỉ có 1 result, giữ result đó và gọi DDGS để top-up.
- Image: 1 usable SearXNG result là đủ; không top-up để giảm upstream traffic.
- Merge ưu tiên result SearXNG trước và dedupe theo canonical URL.

## Cache và singleflight

```env
SEARCH_CACHE_MAX_ENTRIES=256
SEARCH_WEB_CACHE_TTL_SEC=60.0
SEARCH_IMAGE_CACHE_TTL_SEC=120.0
SEARCH_STALE_CACHE_TTL_SEC=900.0
```

Fresh cache trả ngay, không gọi upstream. Stale cache chỉ dùng khi mọi backend khả dụng đều thất bại/skipped. Singleflight coalesce các request đồng thời có cùng `(kind, limit, normalized_query)` thành một pipeline upstream.

## SearXNG engine policy

`searxng/settings.example.yml` đặt:

- global outgoing timeout 3s, ceiling 5s;
- Google CSE web/images timeout 5s;
- 429 suspend engine 3600s;
- CAPTCHA/access denied suspend dài hơn;
- private JSON API vẫn bật, limiter vẫn tắt vì container không public port.

Nếu production đã có `searxng/settings.yml`, phải merge thay đổi từ file example; sửa example không tự cập nhật file đang mount.

## Connection lifecycle

SearXNG dùng shared `httpx.AsyncClient` theo `Settings` để reuse keep-alive/connection pool. `app.main` gọi `close_search_runtimes()` khi shutdown để đóng client deterministic.

## SearXNG update policy

SearXNG là rolling release và engine scraper thay đổi thường xuyên. Production nên stay near latest nhưng không để mutable `latest` tự đổi không kiểm soát:

1. phát hiện candidate mới;
2. validate config/YAML;
3. smoke test JSON web + image;
4. chạy regression suite;
5. candidate pass thì promote exact build/tag đã test;
6. candidate fail thì giữ build production hiện tại.

Security fix hoặc fix liên quan engine đang lỗi được ưu tiên test ngay.

## Audit/debug sau triển khai

Sau mỗi deploy, thực hiện failure injection:

- SearXNG down -> DDGS fallback;
- DDGS down -> SearXNG không bị ảnh hưởng;
- cả hai down -> stale cache hoặc SearchError;
- circuit OPEN -> cooldown -> HALF_OPEN -> CLOSED khi probe thành công;
- Google CSE timeout và Brave 429 không làm crash toàn search;
- burst concurrent query xác nhận singleflight và không leak task/client.

Lặp quy trình:

```text
AUDIT -> FIND BUG -> FAILING REGRESSION TEST -> FIX -> FULL TEST -> DEPLOY -> AUDIT AGAIN
```

Chỉ kết thúc khi `Critical = 0` và `High = 0`. Medium/Low còn lại phải được ghi nhận rõ.
