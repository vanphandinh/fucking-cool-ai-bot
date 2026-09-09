# B.AI integration

B.AI là **AI provider duy nhất** của runtime. Integration dùng OpenAI-compatible Chat Completions:

```text
POST https://api.b.ai/v1/chat/completions
```

Runtime không thêm reasoning knobs không được đảm bảo bởi schema upstream và không tự rotate model.

## Supported promoted models

Allowlist hiện tại:

| Model | Text | Vision | Ghi chú |
|---|---|---|---|
| `qwen3.8-flash` | yes | yes | Default text + vision |
| `mimo-v2.5` | yes | yes | Có thể trả `reasoning_content` cần replay trong cùng provider |
| `hy3` | yes | no | Text-only trong integration |
| `glm-5.3-flash` | yes | yes | Selectable, không auto-fallback |

Promotion/zero-credit status có thể thay đổi upstream. Việc model nằm trong allowlist chỉ có nghĩa integration đã khóa contract tương thích, không đảm bảo pricing vĩnh viễn.

## Configuration

```env
BAI_API_KEY=...
BAI_TEXT_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=30.0

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
MAX_IMAGES_PER_REQUEST=1
```

`BAI_API_KEY` là credential AI duy nhất mà startup yêu cầu cho text runtime.

Không còn `TEXT_PROVIDER_ORDER` hoặc `VISION_PROVIDER_ORDER`; không còn Gemini/Groq/OpenRouter/Cloudflare Workers AI factory trong runtime.

## Text / vision slots

Router vẫn giữ hai provider instances có capability khác nhau:

```text
bai         -> route=text
bai_vision  -> route=vision, supports_vision=True, max_images=1
```

Một text request chỉ chọn `bai`. Một request đúng một ảnh chỉ chọn `bai_vision`. Request nhiều hơn một ảnh không có capable provider và bị chặn với UX B.AI-specific.

## Tool calling

B.AI dùng shared OpenAI-compatible parser và hỗ trợ:

- `web_search`
- `image_search`
- `fetch_url`

Hard safety limits:

- tối đa 8 tool calls/completion;
- tối đa 2 tool calls song song;
- `MAX_TOOL_ROUNDS` điều khiển discovery rounds.

Assistant metadata do chính B.AI trả (`reasoning_details`, `reasoning`, `reasoning_content`) vẫn được replay trong **cùng provider** khi cần cho tool continuation. Việc giữ metadata này không phải cross-provider compatibility.

## Fresh synthesis

Sau khi discovery budget hết, router tạo fresh no-tool context từ:

1. original request messages;
2. bounded untrusted evidence từ successful tool outputs;
3. instruction tổng hợp và không gọi tools.

Structured current-request `tool` messages/tool-call trajectory không được replay vào fresh synthesis.

B.AI provider đặt:

```json
{"tool_choice":"none"}
```

khi request không có tools.

Nếu fresh synthesis vẫn trả tool call, tool đó không thực thi, không retry vô hạn và không fallback sang external AI provider.

## Health behavior

- `401/403`: disable slot tới process restart;
- `429`: cooldown, honor numeric `Retry-After` khi có;
- network/`5xx`: transient health handling;
- local no-tool policy violation: non-transient, không được biến thành cooldown giả;
- success: reset transient state.

Text và vision slot có health state riêng.

## Manual compatibility probe

Probe không chạy trong CI và đọc `BAI_API_KEY` từ environment:

```bash
BAI_API_KEY=... python scripts/probe_bai.py
BAI_API_KEY=... python scripts/probe_bai.py --tools
BAI_API_KEY=... python scripts/probe_bai.py --image ./probe.jpg
```

Probe không in Authorization header. Experimental reasoning flags chỉ dùng để kiểm tra compatibility, không tự động đi vào production runtime.

## Rollout checklist

1. Chạy baseline text probe với model định dùng.
2. Chạy `--tools` và xác nhận tool continuation.
3. Nếu dùng vision, probe đúng một ảnh.
4. Không nâng `max_images=1` nếu chưa có live multi-image compatibility test riêng.
5. Rebuild/restart bot.
6. Smoke text, web-search, direct URL, one-image vision và `/status`.
7. Kiểm tra failure path: B.AI lỗi phải kết thúc request, không phát sinh external AI request.
