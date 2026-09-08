# Đồng bộ `.env` theo `.env.example`

Sau khi `git pull`, chạy từ thư mục gốc của repo:

```bash
python scripts/sync_env.py
```

Script coi `.env.example` là source of truth cho danh sách key, thứ tự, comment và section:

- key còn tồn tại trong cả hai file: giữ nguyên value hiện tại trong `.env`;
- key mới trong `.env.example`: lấy default từ `.env.example`;
- key đã bị xóa khỏi `.env.example`: xóa khỏi `.env`;
- nếu `.env` chưa tồn tại: tạo mới từ `.env.example`;
- duplicate key hoặc dòng assignment không hợp lệ: fail-fast và không ghi đè `.env`;
- nếu kết quả không thay đổi: không rewrite file;
- khi có thay đổi: ghi bằng atomic replace và giữ file mode hiện tại của `.env`.

Ví dụ:

```env
# .env trước khi sync
BOT_TOKEN=real-secret
OLD_SETTING=123
LOG_LEVEL=DEBUG
```

```env
# .env.example mới
BOT_TOKEN=
NEW_SETTING=456
LOG_LEVEL=INFO
```

Sau khi chạy:

```env
BOT_TOKEN=real-secret
NEW_SETTING=456
LOG_LEVEL=DEBUG
```

Không commit `.env`; file này có thể chứa token và secret production.
