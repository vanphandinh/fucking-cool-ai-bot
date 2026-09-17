# Đồng bộ `.env` theo `.env.example`

`.env.example` là complete supported key schema cho deployment hiện tại. Chạy từ repository root:

```bash
python scripts/sync_env.py
```

Synchronizer có contract strict, canonical-only:

- Existing values are preserved only for keys present in `.env.example`.
- Missing canonical keys are added from template defaults.
- Any unknown `.env` assignment aborts sync with exit code `2` and leaves `.env` unchanged.
- Duplicate keys hoặc assignment không hợp lệ cũng fail trước rewrite.
- The synchronizer does not migrate, infer, rename, or copy values between keys.
- A second successful run is idempotent; nếu content không đổi thì file không bị rewrite.
- Khi có thay đổi, file được ghi bằng atomic replace.
- `.env` mới được tạo với mode `0600`; file hiện có bị loại group/other/execute bits.

Permission hardening là một phần của sync vì `.env` chứa Telegram token, provider API keys và các secret runtime khác.

## Canonical provider contract

Runtime và template chỉ dùng plural credential/model pools:

```env
CHAINNODE_API_KEYS=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEYS=
XKIRO_BASE_URL=https://api.xkiro.com/v1
XKIRO_TEXT_MODELS=
XKIRO_VISION_MODELS=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro

PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST=5
```

Một phần tử trong plural pool là hợp lệ. Khi có nhiều model và credential, cả Chainnode và xKiro mở rộng target theo deterministic model-major × credential order. `XKIRO_BASE_URL` mặc định là `https://api.xkiro.com/v1` và được dùng chung bởi runtime và qualification probe.

## Deployment sequence

1. Back up deployment secrets outside the repository.
2. Populate all canonical settings required by the deployment.
3. Remove every assignment that is not present in the deployed `.env.example`.
4. Confirm text/vision provider orders contain only current registered providers.
5. Deploy the canonical-only revision.
6. Run `python scripts/sync_env.py` and require exit `0`.
7. Run it again and verify no content rewrite.
8. Restart/rebuild the bot.
9. Smoke text, vision, primary-provider behavior, and controlled fallback.

Một workflow an toàn trước deploy:

```bash
cp .env /secure/location/env-backup
python scripts/sync_env.py
python scripts/sync_env.py
docker compose up -d --build
```

Không commit `.env` hoặc bản backup chứa secret vào repository.

## Operational checks

Sau sync, kiểm tra các current keys và permission:

```bash
grep -E '^(CHAINNODE_|XKIRO_|PROVIDER_RETRY_|PROVIDER_RECOVERY_|TEXT_PROVIDER_ORDER|VISION_PROVIDER_ORDER|VISION_ENABLED)' .env
stat -c '%a %n' .env
```

Xác nhận provider orders chỉ chứa provider hiện hành, route đang bật có credential/model tương ứng, xKiro chỉ được bật sau khi model đã live-qualified, và lần sync thứ hai không thay đổi nội dung.

Nếu muốn tạm thời vô hiệu hóa xKiro fallback mà không thay config pool:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

Sau đó rebuild/restart service.
