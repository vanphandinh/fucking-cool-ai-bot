# OpenCode provider (Go và Zen)

OpenCode (gói Go hoặc Zen trả trước) là third fallback AI provider cho text và vision, dùng chung `openai-chat` driver với Chainnode và OpenRouter. Profile nằm cuối default provider orders (`chainnode,openrouter,opencode`) và chỉ có active targets khi canonical credential/model pools cho route tương ứng được cấu hình.

## Current configuration

```env
AI_PROVIDERS__OPENCODE__API_KEYS=
AI_PROVIDERS__OPENCODE__BASE_URL=https://opencode.ai/zen/go/v1
AI_PROVIDERS__OPENCODE__TEXT_MODELS=deepseek-v4.1-flash
AI_PROVIDERS__OPENCODE__VISION_MODELS=deepseek-v4-flash-vision-exp
AI_PROVIDERS__OPENCODE__REQUEST_TIMEOUT_SEC=60.0
```

`AI_PROVIDERS__OPENCODE__API_KEYS`, `AI_PROVIDERS__OPENCODE__TEXT_MODELS` và `AI_PROVIDERS__OPENCODE__VISION_MODELS` là canonical runtime pools, giữ nguyên case và thứ tự khai báo. Default model pools trong `.env.example` là các candidate `/chat/completions` đã qualified (`deepseek-v4.1-flash`, `deepseek-v4-flash-vision-exp`); re-qualify khi model xoay vòng hoặc đổi endpoint. Whitespace-only credentials/models được coi là absent.

Nếu OpenCode được chọn cho text và credential pool không rỗng, `AI_PROVIDERS__OPENCODE__TEXT_MODELS` phải có ít nhất một model. Nếu vision bật và OpenCode được chọn cho vision, `AI_PROVIDERS__OPENCODE__VISION_MODELS` cũng phải có ít nhất một model.

`AI_PROVIDERS__OPENCODE__BASE_URL` mặc định trong `.env.example` là `https://opencode.ai/zen/go/v1` cho credential thuộc gói Go; workspace Zen trả trước dùng `https://opencode.ai/zen/v1`. Gửi key Go tới gateway Zen khiến request bị tính vào số dư Zen và trả `HTTP 402` (`Insufficient account funds`) dù quota Go còn trống — lỗi này là cấu hình URL, không phải hết hạn mức. Runtime gọi `POST <BASE_URL>/chat/completions` với `stream=false`. `AI_PROVIDERS__OPENCODE__REQUEST_TIMEOUT_SEC` là per-attempt read timeout, mặc định `60.0` giây; shared transport vẫn dùng bounded connect/write/pool timeouts.

## Endpoint constraint

OpenCode phục vụ model qua nhiều wire protocol. Chỉ các model nằm trên `/chat/completions` của gateway đang dùng (Go: `https://opencode.ai/zen/go/v1/chat/completions`, Zen: `https://opencode.ai/zen/v1/chat/completions`) dùng được với `openai-chat` driver hiện tại:

- model trên `/responses` (ví dụ các GPT, Grok và Muse Spark) cần Responses API driver;
- model trên `/messages` (ví dụ Claude và một số Qwen) cần Anthropic Messages driver;
- model trên `/systemone` (Jev) không phải chat-completions contract.

`GET <BASE_URL>/models` (`https://opencode.ai/zen/go/v1/models` cho Go, `https://opencode.ai/zen/v1/models` cho Zen) là source of truth cho danh sách model và metadata hiện tại; không hard-code model IDs trong source. Một số model free/experimental có thể biến mất hoặc đổi endpoint, nên luôn qualification lại sau khi chọn candidate.

## Session header (bắt buộc với OpenCode Go)

OpenCode Go trả `HTTP 400` với `MissingSessionID` khi request thiếu session id ổn định:

```json
{"type":"error","error":{"type":"MissingSessionID","message":"Request is missing x-opencode-session and cannot be routed efficiently. ..."}}
```

Profile `opencode` khai báo `driver_options.session_header = "x-opencode-session"`. Id là **một id opaque cho mỗi cuộc hội thoại** (`app/ai/session.py`: blake2b của chat id + forum topic với salt ngẫu nhiên mỗi process), nên mọi lượt hỏi-đáp trong cùng cuộc hội thoại — kể cả các vòng tool call trong một lượt và các lần fallback/rotation target — dùng lại đúng một giá trị. Nhờ vậy OpenCode giữ routing và prompt cache ổn định thay vì coi mỗi lượt là một session mới.

Khi caller không truyền conversation id (script, probe, `orchestrator.ask(question=...)`), `openai-chat` fallback về một id `ses_<uuid>` cố định cho concrete target. Header không chứa credential, chat id hay nội dung message; salt đổi khi restart nên id cũng đổi cùng lúc với chat memory trong RAM.

Go cũng yêu cầu client tự nhận diện bằng User-Agent riêng thay vì tên SDK/HTTP library, nên profile gửi `User-Agent: fucking-cool-ai-bot/1.0` qua `driver_options.extra_headers`; `https://opencode.ai/zen/go/v1` tương đương `https://opencode.ai/inference/go` + `/openai/v1`.

## Enable OpenCode

1. Cấu hình `AI_PROVIDERS__OPENCODE__API_KEYS` bằng credential tạo từ OpenCode console (gói Go dùng `https://opencode.ai/zen/go/v1`, workspace Zen trả trước dùng `https://opencode.ai/zen/v1`).
2. Live-qualify candidate trên `/chat/completions` cho text (plain chat, structured tool call, tool continuation) và cho vision (image understanding + vision/tool flow) nếu route vision được dùng.
3. Điền model IDs đã pass vào `AI_PROVIDERS__OPENCODE__TEXT_MODELS` / `AI_PROVIDERS__OPENCODE__VISION_MODELS`.
4. Xác nhận `opencode` nằm trong order mặc định (hoặc đặt order riêng), ví dụ:

```env
TEXT_PROVIDER_ORDER=chainnode,openrouter,opencode
VISION_PROVIDER_ORDER=chainnode,openrouter,opencode
```

5. Chạy `python scripts/sync_env.py` và yêu cầu exit `0`; chạy lần hai để xác nhận idempotence.
6. Rebuild/restart bot và smoke route đã chọn.

Khi `AI_PROVIDERS__OPENCODE__API_KEYS` trống, OpenCode có zero active targets và giữ nguyên hành vi fallback hiện tại. Model vision khai báo `max_images = 1` giống các provider còn lại.

## Target expansion

Concrete targets được tạo theo deterministic model-major × credential order. Với models `m1,m2` và credentials `c1,c2`, thứ tự là:

```text
m1 / cred-1
m1 / cred-2
m2 / cred-1
m2 / cred-2
```

Raw credentials không xuất hiện trong target IDs, logs hoặc errors; identity chỉ dùng alias `cred-1`, `cred-2`. Tổng enabled provider target pool vẫn chịu global runtime cap.

## Recovery scopes

OpenCode profile khai báo recovery override:

- `402`: disable credential scope;
- `403`: disable entitlement (model × credential) scope;
- `429`: cool down credential scope.

Các status còn lại dùng provider-agnostic default: `401` disable credential, `404` disable model, `5xx` bounded provider-family fallback. Retry, recovery-hop và tool budgets vẫn request-bounded; completed tools không chạy lại khi target rotate.

## Rollback

Tắt OpenCode mà không xóa credential/model pools:

```env
TEXT_PROVIDER_ORDER=chainnode,openrouter
VISION_PROVIDER_ORDER=chainnode,openrouter
```

Sau đó rebuild/restart service.

## Credential safety

- không commit `AI_PROVIDERS__OPENCODE__API_KEYS` hoặc bất kỳ raw credential nào;
- không in raw credentials/Authorization headers;
- deployment `.env` phải giữ owner-only permissions.

## Generic provider architecture

OpenCode là một `ProviderProfile` trong `config/ai-providers.toml` dùng `openai-chat` driver. Không có module Python theo vendor, không có branch trong router/retry/health/orchestrator, và profile không khai báo `driver_options.provider_preferences` nên `AI_PROVIDERS__OPENCODE__PROVIDER` không được hỗ trợ.
