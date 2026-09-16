# Đồng bộ `.env` theo `.env.example`

Sau khi `git pull`, chạy từ thư mục gốc:

```bash
python scripts/sync_env.py
```

`.env.example` là source of truth cho danh sách key, thứ tự, comment và section. Script:

- giữ nguyên value hiện tại của key vẫn còn trong template;
- thêm key mới với default từ `.env.example`;
- xoá key không còn trong template;
- migrate legacy provider scalar values sang canonical plural pool fields khi plural field chưa tồn tại;
- migrate các provider-retry legacy aliases sang canonical keys;
- migrate legacy provider orders chứa provider đã retire sang `chainnode,xkiro`;
- không tự thay đổi custom provider order nếu value không chứa provider legacy cần migration;
- hiểu quoted/unquoted dotenv values và inline comments cho các preflight migration checks;
- fail-fast khi có duplicate key, assignment lỗi, quoted provider order không đóng, hoặc migration sẽ làm mất một provider route đang hoạt động;
- không rewrite file khi output không đổi;
- dùng atomic replace khi có thay đổi;
- tạo `.env` mới với mode `0600` và loại group/other/execute bits khỏi file cũ.

Permission hardening là một phần của sync vì `.env` có Telegram token, provider API keys và các secret runtime khác.

## Provider-pool contract

Canonical current provider template uses plural model/credential pools:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEYS=
XKIRO_TEXT_MODELS=
XKIRO_VISION_MODELS=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Chainnode remains primary. xKiro is fallback only after an operator supplies `XKIRO_API_KEYS` and live-qualified model pools.

Legacy scalar fields remain in the template for backward compatibility:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
```

They are migration/backward-compatibility fields, not the current deployment interface. Runtime uses the plural field whenever that plural field is explicitly present. Therefore an explicitly blank plural field intentionally disables scalar fallback. A fresh `.env` created from current `.env.example` already contains the plural fields, so configure the plural fields on new/current deployments.

For legacy deployments where plural fields do not yet exist, sync copies scalar values into the corresponding plural field while preserving case. It does not delete the legacy scalar field, so downgrade/backward compatibility is retained. Running sync again is idempotent.

### Legacy provider-order migration

B.AI has been retired from active runtime. If an existing deployment order still contains its legacy provider token, sync canonicalizes the order to put `chainnode` first and `xkiro` second, then preserves any unrelated custom provider names after them without duplicates. The same rule applies to both text and vision provider orders.

Quoted/commented values are interpreted before deciding whether migration is required. This prevents a quoted retired-provider token from surviving migration and reaching the current registry. Unclosed quoted provider-order values fail before `.env` is rewritten.

If the existing order contains no retired-provider token, sync preserves the operator value exactly. For example:

```text
chainnode          -> chainnode
Foo,chainnode      -> Foo,chainnode
```

This prevents the migration from silently expanding an intentional custom/Chainnode-only order. Exact legacy-token transformations are regression-tested in `tests/test_sync_env.py` and `tests/test_provider_env_migration.py` rather than kept as active deployment examples.

### Fail-safe retirement preflight

The migration evaluates the retired provider using its historical runtime behavior, not only keys explicitly present in `.env`. Historical B.AI text/vision models and provider orders had runtime defaults, so an old deployment with a B.AI API key could have working text and vision even when the model/order overrides were absent from the file.

Before removing that route, sync verifies the target configuration:

- a working legacy text route requires at least one selected replacement text provider with usable credentials and at least one text model;
- when vision is enabled and the target template contains a vision route, a working legacy vision route also requires a selected replacement with usable credentials and at least one vision model;
- setting `VISION_ENABLED=0` explicitly allows retirement without a replacement vision provider;
- quoted-empty, whitespace-only, or commented-empty credentials/models are treated as missing, not as valid replacements.

If either required replacement is missing, sync exits non-zero **before** atomic rewrite, leaving the original `.env` unchanged. Configure Chainnode/xKiro replacement credentials/models first, or explicitly disable vision when that capability is intentionally being removed.

### Credentials are not migrated between services

Obsolete B.AI credential/model/timeout keys disappear because they no longer exist in `.env.example`. Their values are **never** copied into `XKIRO_*`.

Existing Chainnode and xKiro values are preserved independently when their keys remain in the template. For example, a current xKiro pool stays intact while a legacy provider order is canonicalized:

```env
CHAINNODE_API_KEY=chainnode-existing
XKIRO_API_KEYS=xkiro-key-one,xkiro-key-two
XKIRO_TEXT_MODELS=xkiro-text-one,xkiro-text-two
XKIRO_VISION_MODELS=xkiro-vision
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
SEARCH_BACKEND=searxng
```

A deployment that has no xKiro credential receives blank `XKIRO_*` defaults rather than a credential copied from another service.

## Provider retry key migration

Canonical retry names remain:

```env
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
```

Runtime still accepts the verbose aliases:

```env
PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES=
PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER=
PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST=
```

If only a legacy alias exists, sync carries its value to the canonical key. If both forms exist, the canonical value wins. Running sync again is idempotent when nothing else changed.

## Timeouts

`CHAINNODE_REQUEST_TIMEOUT_SEC` and `XKIRO_REQUEST_TIMEOUT_SEC` control provider read timeouts and default to `60.0`. Shared OpenAI-compatible transport still uses `connect=8s`, `write=20s`, and `pool=5s`.

Because sync preserves existing values, an explicitly configured provider timeout is not overwritten just because the template default changed. Adjust production timeouts deliberately rather than coupling them to provider migration.

## Production procedure

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Then inspect the provider/runtime keys and file mode:

```bash
grep -E '^(CHAINNODE_|XKIRO_|PROVIDER_RETRY_|TEXT_PROVIDER_ORDER|VISION_PROVIDER_ORDER|VISION_ENABLED|SEARCH_BACKEND)' .env
stat -c '%a %n' .env
```

Confirm:

- canonical plural Chainnode/xKiro pools contain the intended current values;
- xKiro credentials/models are present only if explicitly configured;
- `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` contain only registered providers;
- enabled text/vision routes have usable replacement credentials/models before retiring a legacy route;
- retry config uses canonical keys;
- obsolete provider credentials are gone;
- Telegram/search/Crawl4AI values remain intact;
- `.env` has no group/other/execute permission bits.

Run sync a second time and verify no content change. Regression coverage also creates a fresh `.env` from the real `.env.example`, configures plural xKiro pools, builds the expected model-major × credential targets, and verifies the second sync is idempotent.

After xKiro has been live-qualified, the normal production order is:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Operational rollback disables xKiro without restoring a retired provider:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

Then rebuild/restart the service.

Do not commit `.env` or `.env*.bak`; these files may contain production secrets.
