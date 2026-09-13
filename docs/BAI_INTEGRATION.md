# B.AI integration

B.AI là **default configured provider** cho text và vision. Runtime registry cũng hỗ trợ optional Chainnode cho text; B.AI không phải architectural singleton. Runtime dùng generic `AIProvider` contract + registry + ordered routing; B.AI là một provider-family adapter trong framework đó.

B.AI dùng OpenAI-compatible Chat Completions:

```text
POST https://api.b.ai/v1/chat/completions
```

Provider-specific behavior như auth, payload parsing, `tool_choice=none`, model allowlist và reasoning replay nằm trong B.AI/OpenAI-compatible adapter; core router không branch theo tên `bai`.

## Supported promoted models

Allowlist hiện tại:

| Model | Text | Vision | Ghi chú |
|---|---|---|---|
| `qwen3.8-flash` | yes | yes | Default text + vision |
| `mimo-v2.5` | yes | yes | Có thể trả `reasoning_content` cần replay trong cùng provider |
| `hy3` | yes | no | Text-only trong integration |
| `glm-5.3-flash` | yes | yes | Selectable, không auto-fallback |

Promotion/zero-credit status có thể thay đổi upstream. Model nằm trong allowlist chỉ có nghĩa integration đã khóa contract tương thích, không đảm bảo pricing vĩnh viễn.

Không có automatic B.AI model rotation/fallback giữa các model allowlist.

## Configuration

```env
BAI_API_KEY=...
BAI_TEXT_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=60.0
TEXT_PROVIDER_ORDER=bai
PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES=2
PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER=3
PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST=5

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
VISION_PROVIDER_ORDER=bai
MAX_IMAGES_PER_REQUEST=1
```

`BAI_REQUEST_TIMEOUT_SEC` là **per-HTTP-attempt read timeout** của B.AI. Shared OpenAI-compatible transport tách các phase còn lại thành `connect=8s`, `write=20s`, `pool=5s`. `QUESTION_TIMEOUT_SEC` vẫn là outer hard deadline cho toàn bộ end-user question, bao gồm tools, retry và fallback. Nếu deployment hiện đang ghi rõ `BAI_REQUEST_TIMEOUT_SEC=30.0`, `scripts/sync_env.py` giữ nguyên value đó; không thay timeout production cùng lúc với rollout retry policy.

`TEXT_PROVIDER_ORDER` và `VISION_PROVIDER_ORDER` là config generic của framework. Default orders đều là `bai`. Text registry hiện có `bai` và optional `chainnode`; vision hiện chỉ có B.AI.

Startup yêu cầu ít nhất một configured text provider có thể build theo `TEXT_PROVIDER_ORDER`; core startup không hard-code `BAI_API_KEY`. Với default order `bai`, thiếu B.AI config dẫn đến không có text provider hợp lệ. Deployment có thể dùng Chainnode text nếu cấu hình đầy đủ và đưa `chainnode` vào text order.

## Provider-family slots

B.AI family factory trả route slots dưới cùng stable provider key:

```text
bai / route=text
bai / route=vision / supports_vision=True / max_images=1
```

Route identity nằm trong `ProviderCapabilities`, không encode bằng tên UI kiểu `bai_vision`.

Một text request với default order chọn B.AI text slot. Một image request trong capability limit chọn B.AI vision slot. Chainnode chỉ là text provider; nếu sau này registry có provider vision khác, router có thể route/fallback theo `VISION_PROVIDER_ORDER` mà không đổi B.AI adapter.

## Tool calling

B.AI dùng shared OpenAI-compatible parser và hỗ trợ schema tool generic của bot:

- `web_search`
- `image_search`
- `fetch_url`

Hard request-wide safety limits:

- tối đa 8 tool calls/request;
- tối đa 2 tool calls chạy song song;
- `MAX_TOOL_ROUNDS` điều khiển discovery rounds.

Tool budget không reset khi router chuyển provider hoặc khi cùng provider thực hiện bounded transport retry.

Assistant metadata do B.AI trả (`reasoning_details`, `reasoning`, `reasoning_content`) chỉ được replay trong **cùng provider attempt** khi cần cho continuation. Portable cross-provider state loại bỏ metadata này. `ToolCall.extra_content` provider-specific cũng không được chuyển sang provider khác.

## Fresh Synthesis

Khi discovery budget đóng/hết, router tạo provider-neutral Fresh Synthesis context từ:

1. original canonical request messages;
2. bounded untrusted evidence từ successful tool outputs;
3. instruction tổng hợp và không gọi tools.

Structured provider-local tool trajectory và vendor metadata không được replay vào fresh synthesis.

B.AI adapter đặt:

```json
{"tool_choice":"none"}
```

khi request không có tools.

Nếu B.AI vẫn trả tool call sau khi tools đã tắt, tool đó không thực thi và B.AI attempt fail với non-transient `ProviderError`. Với default B.AI-only text order, request kết thúc sau failure đó. Nếu Chainnode hoặc provider text khác được cấu hình sau B.AI trong order, router có thể thử candidate tiếp theo bằng **cùng closed tool budget** và generic Fresh Synthesis context.

## Bounded cyclic transport retry

Shared `OpenAICompatProvider` chỉ thực hiện **một HTTP attempt** mỗi lần `chat()` được gọi và phân loại transport failure. Router sở hữu retry policy request-scoped:

| Failure | Policy |
|---|---|
| `ConnectError` | retry cùng provider khi còn budget; sau đó rotate |
| `ConnectTimeout` | retry cùng provider khi còn budget; sau đó rotate |
| `ReadTimeout` | ưu tiên provider khác đang eligible; nếu không có thì retry cùng provider |
| `WriteTimeout` | không có cyclic revisit mới |
| `PoolTimeout` | không có cyclic revisit mới |
| HTTP `401/403/429/4xx/5xx` | giữ policy hiện tại; không có cyclic revisit mới |

Ba request-scoped budget mặc định là `consecutive=2`, `per-provider cumulative=3`, `per-request cumulative=5`. Một `provider.chat()` hợp lệ — kể cả response chứa tool call — reset **chỉ** consecutive counter của đúng provider đó. Provider cumulative và request cumulative counters không reset trong cùng end-user request.

Với `TEXT_PROVIDER_ORDER=chainnode,bai`, cyclic routing có thể đi:

```text
chainnode ReadTimeout
-> bai ReadTimeout
-> chainnode success
```

Nếu cả hai tiếp tục ReadTimeout với defaults:

```text
chainnode #1 fail
-> bai #1 fail
-> chainnode #2 fail/exhaust
-> bai #2 fail/exhaust
-> AllProvidersFailed
```

Retry cùng provider diễn ra tại đúng model HTTP continuation bị lỗi, với cùng `messages` và active tool schema. Rotation/wrap không reset `_ToolBudget`, portable messages, successful tool outputs hoặc closed state; tool đã hoàn thành không chạy lại chỉ vì provider đổi.

ReadTimeout alternative được tính lại **tại thời điểm failure** trên toàn bộ cyclic candidate ring, dựa trên capability, shared health hiện tại và request-scoped budget hiện tại.

## Health và ordered fallback

B.AI slot health behavior:

- `401/403`: disable slot tới process restart;
- `429`: cooldown, honor numeric `Retry-After` khi có;
- network/`5xx`: transient health handling;
- local protocol/no-tool policy violation: provider-local failure, không tạo transient cooldown giả nếu marked non-transient;
- success: reset transient state.

Text và vision slots có health state riêng.

Cyclic-retryable transport streak được giữ pending trong request. Nếu provider recover bằng một valid chat response, pending error bị clear và shared health không bị increment. Nếu provider bị exhaust/block hoặc provider khác hoàn tất request trước khi nó recover, unresolved streak được flush **một logical health failure** đúng một lần. `401/403/429` và non-cyclic failures vẫn áp dụng shared-health behavior ngay như trước.

Fallback statistics đếm provider transition, không đếm same-provider attempts. Vì vậy `chainnode -> bai -> chainnode` ghi hai fallback transitions; retry `chainnode -> chainnode` không ghi fallback.

## Thêm provider khác bên cạnh B.AI

Chainnode text provider đã là một ví dụ registered optional provider; xem [`CHAINNODE.md`](CHAINNODE.md). Với provider mới khác, B.AI adapter không cần sửa. Provider mới nên:

1. implement `AIProvider` trực tiếp hoặc reuse `OpenAICompatProvider`;
2. thêm namespaced settings;
3. tạo provider-family factory;
4. register factory trong `PROVIDER_FACTORIES`;
5. khai báo capabilities và thêm key vào text/vision order;
6. pass generic registry/routing/portability tests + provider-specific tests.

## Manual compatibility probe

Probe B.AI không chạy trong CI và đọc `BAI_API_KEY` từ environment:

```bash
BAI_API_KEY=... python scripts/probe_bai.py
BAI_API_KEY=... python scripts/probe_bai.py --tools
BAI_API_KEY=... python scripts/probe_bai.py --image ./probe.jpg
```

Probe không in Authorization header. Experimental reasoning flags chỉ dùng để kiểm tra compatibility, không tự động đi vào production runtime.

## Rollout checklist

1. Chạy baseline text probe với model B.AI định dùng.
2. Chạy `--tools` và xác nhận tool continuation.
3. Nếu dùng vision, probe đúng một ảnh.
4. Không nâng B.AI `max_images=1` nếu chưa có live multi-image compatibility test riêng.
5. Xác nhận `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` chỉ chứa provider đã register.
6. Deploy retry code **không đổi timeout production cùng lúc**.
7. Smoke text, web-search, direct URL, one-image vision và `/status`.
8. Quan sát `ConnectTimeout -> same-provider retry -> success/fallback`.
9. Với multiple text providers, quan sát `ReadTimeout -> eligible alternative` và natural `provider A -> provider B -> provider A` wrap khi xảy ra.
10. Xác nhận completed tool evidence được reuse nhưng tool executor không chạy lần hai sau rotation/wrap.
11. Xác nhận all-provider failure dừng ở configured budgets và không spin vô hạn.

Rollback retry code bằng revert PR hoặc deploy commit trước; không cần đổi timeout để rollback.
