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
- output không đổi: không rewrite nội dung;
- có thay đổi: atomic replace;
- file `.env` mới được tạo với mode `0600`;
- file `.env` đã tồn tại được loại bỏ execute/group/other permission bits, kể cả khi nội dung không đổi; owner permissions chặt hơn `0600` vẫn được giữ nguyên.

Permission hardening là một phần của sync vì `.env` có thể chứa Telegram token, provider API key và các secret runtime khác. Script không copy permission `0644` của `.env.example` sang secret file production.

## Migration trên generic provider framework

Text provider registry hiện hỗ trợ B.AI và optional Chainnode. Default route vẫn là B.AI; vision hiện dùng B.AI. `.env.example` giữ generic routing keys:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

và các Chainnode keys:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0
```

Credential/model variables của Gemini, Groq, OpenRouter và Cloudflare Workers AI không còn trong template, nên `sync_env.py` sẽ loại chúng khỏi `.env`. Các `CHAINNODE_*` key mới được thêm từ template nhưng không tự bật provider vì API key/model mặc định để trống và default text order vẫn là `bai`.

Ví dụ `.env` cũ:

```env
BAI_API_KEY=real-bai-secret
BAI_TEXT_MODEL=mimo-v2.5
BAI_REQUEST_TIMEOUT_SEC=30.0
BAI_VISION_MODEL=qwen3.8-flash
TEXT_PROVIDER_ORDER=bai,gemini,groq
VISION_PROVIDER_ORDER=bai,groq
GEMINI_API_KEY=old-secret
GROQ_API_KEY=old-secret
OPENROUTER_API_KEY=old-secret
CLOUDFLARE_ACCOUNT_ID=old-account
CLOUDFLARE_API_TOKEN=old-secret
SEARCH_BACKEND=searxng
```

Sau:

```bash
python scripts/sync_env.py
```

các value của key vẫn tồn tại trong template được giữ nguyên; Chainnode keys mới được thêm theo default:

```env
BAI_API_KEY=real-bai-secret
BAI_TEXT_MODEL=mimo-v2.5
BAI_REQUEST_TIMEOUT_SEC=30.0
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0
BAI_VISION_MODEL=qwen3.8-flash
TEXT_PROVIDER_ORDER=bai,gemini,groq
VISION_PROVIDER_ORDER=bai,groq
SEARCH_BACKEND=searxng
```

Trong khi credential/model keys của provider đã xóa biến mất.

### Timeout mới không ghi đè value production cũ

`BAI_REQUEST_TIMEOUT_SEC` và `CHAINNODE_REQUEST_TIMEOUT_SEC` hiện điều khiển **read timeout** của provider, default `60.0`. Shared OpenAI-compatible transport dùng `connect=8s`, `write=20s`, `pool=5s`.

Vì `sync_env.py` giữ existing values, nếu production đã có:

```env
BAI_REQUEST_TIMEOUT_SEC=30.0
```

thì chạy sync vẫn giữ `30.0`. Muốn áp dụng default read timeout mới, operator phải sửa thành:

```env
BAI_REQUEST_TIMEOUT_SEC=60.0
```

Tương tự với `CHAINNODE_REQUEST_TIMEOUT_SEC` nếu Chainnode đã được cấu hình từ trước.

### Vì sao order cũ không tự bị sửa?

`sync_env.py` không diễn giải semantic của value; nó chỉ đồng bộ key set và giữ value hiện tại. Vì vậy một order cũ như:

```env
TEXT_PROVIDER_ORDER=bai,gemini
```

vẫn được giữ nếu key `TEXT_PROVIDER_ORDER` còn trong `.env.example`.

Đây là intentional: script không silently thay đổi routing intent của operator. Generic provider registry/startup sẽ validate order. Nếu order chứa provider chưa register, startup fail rõ ràng với lỗi `AI provider chưa được đăng ký`.

Text provider keys hiện được register là `bai` và `chainnode`; vision provider hiện là `bai`. Trước khi restart sau migration, bỏ các provider đã bị xóa khỏi order. Default an toàn vẫn là:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

Nếu muốn bật Chainnode text với B.AI fallback, cấu hình `CHAINNODE_API_KEY`, `CHAINNODE_TEXT_MODEL` rồi dùng order phù hợp, ví dụ `chainnode,bai`; xem [`CHAINNODE.md`](CHAINNODE.md).

## Quy trình production khuyến nghị

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Repo ignore `.env`, `.env.bak` và `.env.*.bak`; Docker build context cũng loại các backup này. Dù vậy, backup vẫn chứa secret production nên không copy/upload/chia sẻ file này ra ngoài máy vận hành.

Sau đó kiểm tra ít nhất:

```bash
grep -E '^(BAI_|CHAINNODE_|TEXT_PROVIDER_ORDER|VISION_PROVIDER_ORDER|SEARCH_BACKEND)' .env
stat -c '%a %n' .env
```

Xác nhận:

- B.AI secret/model/timeout values vẫn đúng;
- Chainnode values đúng nếu provider này được bật; key để trống là bình thường khi không dùng;
- `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` chỉ chứa provider đã register;
- AI read timeout đã là `60.0` nếu deployment muốn áp dụng default mới;
- Telegram/search/Crawl4AI values quan trọng vẫn còn;
- credential variables của provider đã xóa không còn;
- `.env` không có group/other/execute permission bits (normal new-file mode là `600`).

Sau đó mới rebuild/restart service.

Không commit `.env` hoặc file backup `.env*.bak`; chúng có thể chứa token/secret production.
