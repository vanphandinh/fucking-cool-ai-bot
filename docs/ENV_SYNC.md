# Đồng bộ `.env` theo `.env.example`

Sau khi `git pull`, chạy từ thư mục gốc:

```bash
python scripts/sync_env.py
```

Script coi `.env.example` là source of truth cho danh sách key, thứ tự, comment và section:

- key còn tồn tại trong cả hai file: giữ nguyên value hiện tại trong `.env`;
- key mới: lấy default từ `.env.example`;
- key đã bị xóa khỏi `.env.example`: xóa khỏi `.env`;
- `.env` chưa tồn tại: tạo mới từ `.env.example`;
- duplicate key / assignment lỗi: fail-fast, không ghi đè file;
- output không đổi: không rewrite;
- có thay đổi: atomic replace và giữ file mode.

## Migration B.AI-only

Khi nâng từ bản multi-provider sang B.AI-only, `.env.example` không còn các key AI provider ngoài B.AI hoặc provider-order variables.

Ví dụ `.env` cũ:

```env
BAI_API_KEY=real-bai-secret
BAI_TEXT_MODEL=qwen3.8-flash
GEMINI_API_KEY=old-secret
GROQ_API_KEY=old-secret
OPENROUTER_API_KEY=old-secret
CLOUDFLARE_ACCOUNT_ID=old-account
CLOUDFLARE_API_TOKEN=old-secret
TEXT_PROVIDER_ORDER=bai,gemini,groq,cloudflare,openrouter
VISION_PROVIDER_ORDER=bai,gemini
SEARCH_BACKEND=searxng
```

Sau `python scripts/sync_env.py`, B.AI và unrelated settings được giữ:

```env
BAI_API_KEY=real-bai-secret
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
SEARCH_BACKEND=searxng
```

Các key AI provider cũ biến mất vì không còn trong `.env.example`.

Nên backup `.env` trước production migration:

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Không commit `.env` hoặc `.env.bak`; chúng có thể chứa token/secret production.
