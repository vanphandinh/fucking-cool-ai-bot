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
- Nếu caller bị cancel trong lúc giữ HALF_OPEN probe slot, slot được release mà không increment failure count hoặc mở lại circuit; request sau vẫn có thể làm probe.

## Search discovery vs URL reading

Hai pipeline độc lập về trách nhiệm:

```text
Search discovery: SearXNG -> DDGS fallback
URL reading:      X-specific -> Crawl4AI -> generic reader
```

Crawl4AI chỉ render/extract URL cho `fetch_url`; nó không thay thế `app/search/router.py`, không tham gia ranking/search cache/circuit breaker/singleflight/source policy và không được cấu hình LLM provider. Production dùng `POST /crawl`, không dùng `/md?f=fit` hoặc LLM filter path.

Bot vẫn chạy `reader.validate_public_url()` trước khi gửi URL sang Crawl4AI. Nếu Crawl4AI timeout/network/401/403/429/5xx/malformed/failed/empty thì chỉ fallback một lần sang generic reader. `CancelledError` propagate và không khởi chạy fallback. X/Twitter specialized reader vẫn có priority cao hơn Crawl4AI.

Chi tiết deployment/rollback: [CRAWL4AI_INTEGRATION.md](CRAWL4AI_INTEGRATION.md).

## Timeout budget

Các timeout search độc lập với timeout AI provider:

```env
SEARXNG_TIMEOUT_SEC=7.0
DDGS_TIMEOUT_SEC=8.0
SEARCH_TOTAL_TIMEOUT_SEC=15.0
```

Router tạo một deadline tổng. Trước mỗi backend, effective timeout là giá trị nhỏ hơn giữa timeout backend và budget còn lại.

Crawl4AI dùng budget riêng cho URL reading:

```env
CRAWL4AI_TIMEOUT_SEC=25.0
```

Giá trị này phải dương và nhỏ hơn `QUESTION_TIMEOUT_SEC` khi Crawl4AI thực sự active (`CRAWL4AI_ENABLED=1` cùng URL/token không rỗng).

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

Chỉ một request được giữ HALF_OPEN probe slot tại một thời điểm. Web và image có circuit riêng; DDGS và SearXNG cũng có circuit riêng.

Cancellation là neutral đối với backend health: `_attempt()` gọi `record_cancelled()` rồi re-raise `CancelledError`. Nếu request bị cancel trong HALF_OPEN, `probe_in_flight` được release nhưng state vẫn HALF_OPEN, vì caller cancellation không chứng minh backend healthy hoặc unhealthy. Request kế tiếp có thể lấy lại probe slot.

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

Fresh cache trả ngay, không gọi upstream. Stale cache chỉ dùng khi không có backend nào trả thành công trong pipeline hiện tại; nếu một backend trả thành công nhưng kết quả rỗng, router trả `[]` thay vì thay thế bằng stale data. Singleflight coalesce các request đồng thời có cùng `(kind, limit, normalized_query)` thành một pipeline upstream.

Khi waiter cuối cùng rời một flight mà upstream task chưa xong, singleflight cancel upstream task để không để công việc mồ côi chạy tiếp.

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

Crawl4AI cũng dùng một shared `httpx.AsyncClient` process-local với `trust_env=False`; `app.main` gọi `close_crawl4ai_client()` khi shutdown.

## SearXNG update policy

SearXNG là rolling release và engine scraper thay đổi thường xuyên. Production nên stay near latest nhưng không để mutable `latest` tự đổi không kiểm soát:

1. phát hiện candidate mới;
2. validate config/YAML;
3. smoke test JSON web + image;
4. chạy regression suite;
5. candidate pass thì promote exact build/tag đã test qua `SEARXNG_IMAGE`;
6. candidate fail thì giữ build production hiện tại.

Compose có fallback `${SEARXNG_IMAGE:-ghcr.io/searxng/searxng:latest}` cho setup ban đầu; production ổn định nên set `SEARXNG_IMAGE` về tag/digest đã test thay vì dựa lâu dài vào mutable `latest`.

Security fix hoặc fix liên quan engine đang lỗi được ưu tiên test ngay.

## Audit/debug sau triển khai

Sau mỗi deploy, thực hiện failure injection:

- SearXNG down -> DDGS fallback;
- DDGS down -> SearXNG không bị ảnh hưởng;
- cả hai down -> stale cache hoặc SearchError;
- circuit OPEN -> cooldown -> HALF_OPEN -> CLOSED khi probe thành công;
- HALF_OPEN probe bị caller cancel -> slot được release, state không bị ghi thành backend failure, request sau probe lại được;
- Google CSE timeout và Brave 429 không làm crash toàn search;
- burst concurrent query xác nhận singleflight và không leak task/client;
- Crawl4AI down/wrong token/timeout -> generic reader một lần;
- `CRAWL4AI_ENABLED=0` -> Crawl4AI không được gọi;
- private/localhost URL -> blocked trước mọi reader;
- X status -> specialized reader vẫn chạy trước Crawl4AI;
- cancellation -> không tạo generic fallback.

Lặp quy trình:

```text
AUDIT -> FIND BUG -> FAILING REGRESSION TEST -> FIX -> FULL TEST -> DEPLOY -> AUDIT AGAIN
```

Chỉ kết thúc khi `Critical = 0` và `High = 0`. Medium/Low còn lại phải được ghi nhận rõ.
