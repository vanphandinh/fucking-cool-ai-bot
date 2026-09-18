# xKiro fallback provider

xKiro là secondary AI provider cho text và vision. Chainnode giữ vai trò production primary; xKiro chỉ có active targets khi canonical credential/model pools cho route tương ứng được cấu hình.

## Current configuration

```env
AI_PROVIDERS__XKIRO__API_KEYS=
AI_PROVIDERS__XKIRO__BASE_URL=https://api.xkiro.com/v1
AI_PROVIDERS__XKIRO__TEXT_MODELS=
AI_PROVIDERS__XKIRO__VISION_MODELS=
AI_PROVIDERS__XKIRO__REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

`AI_PROVIDERS__XKIRO__API_KEYS`, `AI_PROVIDERS__XKIRO__TEXT_MODELS`, và `AI_PROVIDERS__XKIRO__VISION_MODELS` là canonical runtime pools. Model pools được để trống trong `.env.example` vì model IDs phải được live-qualified trước khi đưa vào deployment.

`AI_PROVIDERS__XKIRO__BASE_URL` mặc định là `https://api.xkiro.com/v1`. Runtime gọi OpenAI-compatible endpoint dưới base URL này và gửi `stream=false`. `AI_PROVIDERS__XKIRO__REQUEST_TIMEOUT_SEC` là per-attempt read timeout, mặc định `60.0` giây.

Nếu xKiro được chọn cho text và credential pool không rỗng, `AI_PROVIDERS__XKIRO__TEXT_MODELS` phải có ít nhất một model. Nếu vision bật và xKiro được chọn cho vision, `AI_PROVIDERS__XKIRO__VISION_MODELS` cũng phải có ít nhất một model. Whitespace-only credentials/models được coi là absent.

## Target expansion

Concrete targets được tạo theo deterministic model-major × credential order. Với models `m1,m2` và credentials `c1,c2`, thứ tự là:

```text
m1 / cred-1
m1 / cred-2
m2 / cred-1
m2 / cred-2
```

Raw credentials không được đưa vào target IDs, logs hoặc errors. Tổng enabled provider target pool vẫn chịu global runtime cap.

## Live qualification

`GET <AI_PROVIDERS__XKIRO__BASE_URL>/models` là source of truth cho candidate hiện tại. Không hard-code xKiro runtime model IDs trong source; các model đã qualify được cấu hình qua plural deployment pools.

Text candidate phải pass catalog gates:

```text
exact model ID exists
access_tier == free
pricing.input == 0
pricing.output == 0
capabilities.tools == true
```

Vision candidate còn phải có `capabilities.vision == true`. Candidate sau đó phải pass live plain-chat, structured-tool-call và tool-continuation probes; vision candidate phải pass image understanding và vision/tool flow.

## Probe authentication

Qualification probe dùng canonical pool `AI_PROVIDERS__XKIRO__API_KEYS`. Vì probe là single-credential qualification tool, nó split pool theo dấu phẩy, trim whitespace, bỏ phần tử blank, giữ nguyên order/case và dùng credential hợp lệ đầu tiên.

```bash
export AI_PROVIDERS__XKIRO__API_KEYS='...'
export AI_PROVIDERS__XKIRO__BASE_URL='https://api.xkiro.com/v1'
python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Nếu `AI_PROVIDERS__XKIRO__BASE_URL` unset hoặc blank, probe dùng shared default `https://api.xkiro.com/v1`. Probe emit JSONL, không log API key/Authorization header, và xử lý `429 Retry-After` trong bounded wait/retry policy.

Chỉ sau khi candidate pass mọi gate mới cấu hình model ID vào `AI_PROVIDERS__XKIRO__TEXT_MODELS` hoặc `AI_PROVIDERS__XKIRO__VISION_MODELS`.

## Recovery scopes

Current xKiro resource recovery:

- `401` / `402`: disable failed credential scope;
- `403`: disable model × credential entitlement scope;
- `429`: cool down failed credential scope;
- `404`: disable failed model scope across credential siblings;
- `5xx`: bounded provider-family fallback.

Completed tools không được chạy lại khi target/provider rotate; retry, recovery-hop và tool budgets vẫn request-bounded.

## Rollout and smoke

1. Cấu hình `AI_PROVIDERS__XKIRO__API_KEYS` và live-qualify current text/vision candidates; probe dùng credential non-blank đầu tiên trong pool.
2. Cấu hình `AI_PROVIDERS__XKIRO__TEXT_MODELS`, `AI_PROVIDERS__XKIRO__VISION_MODELS` và xác nhận `AI_PROVIDERS__XKIRO__BASE_URL`.
3. Giữ provider order mong muốn, ví dụ:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

4. Run `python scripts/sync_env.py` và yêu cầu exit `0`; chạy lần hai để xác nhận idempotence.
5. Rebuild/restart bot, smoke Chainnode primary và controlled xKiro fallback.

Operational rollback để tắt xKiro:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

## Credential safety

- không commit `AI_PROVIDERS__XKIRO__API_KEYS` hoặc bất kỳ raw credential nào;
- không in raw credentials/Authorization headers;
- target identity chỉ dùng opaque aliases như `cred-1`;
- deployment `.env` phải giữ owner-only permissions.


## Generic provider architecture

xKiro là một `ProviderProfile` trong `config/ai-providers.toml` dùng `openai-chat` driver. Các khác biệt recovery 402/403/429 là declarative catalog data; router và `recovery.py` không branch theo vendor.
