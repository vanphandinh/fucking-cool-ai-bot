# Chainnode primary text and vision provider

Chainnode là production primary AI provider cho cả text và vision. Router giữ text/vision target pools riêng để mỗi route có thể dùng model khác nhau trong cùng provider family.

## Current configuration

```env
CHAINNODE_API_KEYS=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

`CHAINNODE_API_KEYS`, `CHAINNODE_TEXT_MODELS`, và `CHAINNODE_VISION_MODELS` là canonical runtime pools. Một phần tử là hợp lệ; nhiều phần tử được phân tách bằng dấu phẩy và giữ nguyên case.

Khi Chainnode được chọn cho text và credential pool không rỗng, `CHAINNODE_TEXT_MODELS` phải có ít nhất một model. Khi vision bật và Chainnode được chọn cho vision, `CHAINNODE_VISION_MODELS` cũng phải có ít nhất một model.

`CHAINNODE_BASE_URL` mặc định là `https://dn.chainno.de/v1`. `CHAINNODE_REQUEST_TIMEOUT_SEC` là per-attempt read timeout và mặc định `60.0` giây. Shared OpenAI-compatible transport vẫn dùng bounded connect/write/pool timeouts.

## Target expansion

Concrete targets được tạo theo deterministic model-major × credential order. Với text models `m1,m2` và credentials `c1,c2`, thứ tự là:

```text
m1 / cred-1
m1 / cred-2
m2 / cred-1
m2 / cred-2
```

Vision pool dùng cùng quy tắc. Runtime identities chỉ chứa opaque credential IDs như `cred-1`; raw API keys không xuất hiện trong target IDs, logs hoặc status.

## Routing and recovery

Production order mặc định:

```text
text:   Chainnode -> xKiro
vision: Chainnode -> xKiro
```

Chainnode recovery scopes giữ bounded behavior hiện tại:

- `401`: disable failed credential scope;
- `429`: cool down failed model scope rồi rotate sang eligible sibling target;
- `402`, `403`, `5xx`: giữ provider-family fallback semantics hiện hành;
- transport retry/recovery budgets là request-bounded và không tăng theo số credential siblings;
- completed tool calls không được chạy lại khi target thay đổi;
- target eligibility được revalidate ngay trước network attempt.

Cả text và vision dùng shared OpenAI-compatible adapter với non-stream request (`stream=false`). Vision giữ OpenAI-compatible `image_url` data-URL shape và current `max_images=1`.

## Qualification probes

Text/tool probe:

```bash
python scripts/probe_chainnode.py \
  --models cl/cline-free/deepseek-v4.1-flash
```

Vision probe:

```bash
python scripts/probe_chainnode_vision.py \
  --models cl/cline-free/muse-spark-1.3-contributor,cl/cline-free/deepseek-v4.1-flash
```

Credentials phải được cung cấp qua environment/deployment secret mechanism, không qua command-line arguments và không commit vào repository.

## Production smoke

Sau deploy:

1. kiểm tra text request dùng healthy Chainnode text target đầu tiên;
2. kiểm tra one-image request dùng healthy Chainnode vision target đầu tiên;
3. kiểm tra structured tools và continuation không duplicate execution;
4. thực hiện controlled target/provider fallback smoke;
5. khôi phục primary target và xác nhận normal traffic quay lại Chainnode.

Operational rollback để tắt xKiro fallback:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```
