# OpenRouter fallback provider

OpenRouter là secondary AI provider cho text và vision. Chainnode giữ vai trò production primary; OpenRouter chỉ có active targets khi canonical credential/model pools cho route tương ứng được cấu hình.

## Current configuration

```env
AI_PROVIDERS__OPENROUTER__API_KEYS=
AI_PROVIDERS__OPENROUTER__BASE_URL=https://openrouter.ai/api/v1
AI_PROVIDERS__OPENROUTER__TEXT_MODELS=
AI_PROVIDERS__OPENROUTER__VISION_MODELS=
AI_PROVIDERS__OPENROUTER__REQUEST_TIMEOUT_SEC=60.0
AI_PROVIDERS__OPENROUTER__PROVIDER=

TEXT_PROVIDER_ORDER=chainnode,openrouter
VISION_PROVIDER_ORDER=chainnode,openrouter
```

`AI_PROVIDERS__OPENROUTER__API_KEYS`, `AI_PROVIDERS__OPENROUTER__TEXT_MODELS`, và `AI_PROVIDERS__OPENROUTER__VISION_MODELS` là canonical runtime pools. Model pools được để trống trong `.env.example` vì model IDs phải được live-qualified trước khi đưa vào deployment.

`AI_PROVIDERS__OPENROUTER__BASE_URL` mặc định là `https://openrouter.ai/api/v1`. Runtime gọi OpenAI-compatible endpoint dưới base URL này và gửi `stream=false`. `AI_PROVIDERS__OPENROUTER__REQUEST_TIMEOUT_SEC` là per-attempt read timeout, mặc định `60.0` giây.

Nếu OpenRouter được chọn cho text và credential pool không rỗng, `AI_PROVIDERS__OPENROUTER__TEXT_MODELS` phải có ít nhất một model. Nếu vision bật và OpenRouter được chọn cho vision, `AI_PROVIDERS__OPENROUTER__VISION_MODELS` cũng phải có ít nhất một model. Whitespace-only credentials/models được coi là absent.

## Provider routing

`AI_PROVIDERS__OPENROUTER__PROVIDER` giới hạn upstream provider mà OpenRouter được phép dùng và ưu tiên theo đúng thứ tự khai báo. Giá trị là danh sách slug ngăn bằng dấu phẩy:

```env
AI_PROVIDERS__OPENROUTER__PROVIDER=deepinfra,together
```

Mỗi request chat-completions của target OpenRouter sẽ mang thêm:

```json
{
  "provider": {
    "only": ["deepinfra", "together"],
    "order": ["deepinfra", "together"]
  }
}
```

- Để trống (default) = không gửi field `provider`, OpenRouter giữ routing mặc định.
- `only` là allowlist cứng: OpenRouter không dùng provider ngoài danh sách. `order` là thứ tự ưu tiên trong allowlist đó. Danh sách được trim, bỏ phần tử rỗng và bỏ trùng.
- Account-wide allowed-provider settings của OpenRouter vẫn là ceiling; request `only` chỉ thu hẹp trong đó. Nếu không còn endpoint nào thỏa cả hai, OpenRouter trả `404` và router xử lý như lỗi model-scope.
- Thuộc tính áp dụng cho mọi lượt của target: text, vision, tool rounds và Fresh Synthesis.
- Thuộc tính chỉ hợp lệ với provider khai báo `driver_options.provider_preferences = true` trong `config/ai-providers.toml` (hiện là OpenRouter). Đặt `AI_PROVIDERS__<ID>__PROVIDER` cho provider khác sẽ fail validation thay vì gửi field lạ.

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

`GET <AI_PROVIDERS__OPENROUTER__BASE_URL>/models` là source of truth cho candidate hiện tại. Không hard-code OpenRouter runtime model IDs trong source; các model đã qualify được cấu hình qua plural deployment pools.

Text candidate phải pass catalog gates theo schema `/models` của OpenRouter:

```text
exact model ID exists
pricing.prompt == 0
pricing.completion == 0
supported_parameters includes "tools"
```

Vision candidate còn phải có `"image"` trong `architecture.input_modalities`. Candidate sau đó phải pass live plain-chat, structured-tool-call và tool-continuation probes; vision candidate phải pass image understanding và vision/tool flow.

Probe gửi kèm `AI_PROVIDERS__OPENROUTER__PROVIDER` khi biến này được cấu hình, nên qualification chạy trên đúng upstream provider mà deployment sẽ dùng.

## Probe authentication

Qualification probe dùng canonical pool `AI_PROVIDERS__OPENROUTER__API_KEYS`. Vì probe là single-credential qualification tool, nó split pool theo dấu phẩy, trim whitespace, bỏ phần tử blank, giữ nguyên order/case và dùng credential hợp lệ đầu tiên.

```bash
export AI_PROVIDERS__OPENROUTER__API_KEYS='...'
export AI_PROVIDERS__OPENROUTER__BASE_URL='https://openrouter.ai/api/v1'
python scripts/probe_openrouter.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Nếu `AI_PROVIDERS__OPENROUTER__BASE_URL` unset hoặc blank, probe dùng shared default `https://openrouter.ai/api/v1`. Probe emit JSONL, không log API key/Authorization header, và xử lý `429 Retry-After` trong bounded wait/retry policy.

Chỉ sau khi candidate pass mọi gate mới cấu hình model ID vào `AI_PROVIDERS__OPENROUTER__TEXT_MODELS` hoặc `AI_PROVIDERS__OPENROUTER__VISION_MODELS`.

## Recovery scopes

Current OpenRouter resource recovery:

- `401` / `402`: disable failed credential scope;
- `403`: disable model × credential entitlement scope;
- `429`: cool down failed credential scope;
- `404`: disable failed model scope across credential siblings — bao gồm trường hợp `PROVIDER` allowlist không còn endpoint phù hợp cho model;
- `5xx`: bounded provider-family fallback.

Completed tools không được chạy lại khi target/provider rotate; retry, recovery-hop và tool budgets vẫn request-bounded.

## Rollout and smoke

1. Cấu hình `AI_PROVIDERS__OPENROUTER__API_KEYS` và live-qualify current text/vision candidates; probe dùng credential non-blank đầu tiên trong pool.
2. Cấu hình `AI_PROVIDERS__OPENROUTER__TEXT_MODELS`, `AI_PROVIDERS__OPENROUTER__VISION_MODELS` và xác nhận `AI_PROVIDERS__OPENROUTER__BASE_URL`.
3. Nếu cần giới hạn upstream provider, đặt `AI_PROVIDERS__OPENROUTER__PROVIDER` và chạy lại probe để qualification khớp routing.
4. Giữ provider order mong muốn, ví dụ:

```env
TEXT_PROVIDER_ORDER=chainnode,openrouter
VISION_PROVIDER_ORDER=chainnode,openrouter
```

5. Run `python scripts/sync_env.py` và yêu cầu exit `0`; chạy lần hai để xác nhận idempotence.
6. Rebuild/restart bot, smoke Chainnode primary và controlled OpenRouter fallback.

Operational rollback để tắt OpenRouter:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

## Credential safety

- không commit `AI_PROVIDERS__OPENROUTER__API_KEYS` hoặc bất kỳ raw credential nào;
- không in raw credentials/Authorization headers;
- target identity chỉ dùng opaque aliases như `cred-1`;
- deployment `.env` phải giữ owner-only permissions.

## Generic provider architecture

OpenRouter là một `ProviderProfile` trong `config/ai-providers.toml` dùng `openai-chat` driver. Các khác biệt recovery 402/403/429 và cờ `driver_options.provider_preferences` là declarative catalog data; router và `recovery.py` không branch theo vendor.
