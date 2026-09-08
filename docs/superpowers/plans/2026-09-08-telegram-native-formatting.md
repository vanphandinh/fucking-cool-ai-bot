# Telegram Native Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển phần trả lời chính của bot từ plain text sang Telegram-native HTML an toàn, tự nhiên, dễ đọc, không để lộ Markdown thô như `**bold**`, đồng thời vẫn giữ khả năng chia message dài và fallback an toàn.

**Architecture:** Model được hướng dẫn sinh một tập con Telegram HTML có kiểm soát. Output đi qua một lớp chuẩn hóa/sanitize thuần Python, sau đó qua splitter theo độ dài UTF-16 của phần text nhìn thấy, với cơ chế đóng/mở lại tag để mỗi chunk luôn là HTML hợp lệ. Handler gửi mọi chunk bằng `parse_mode="HTML"`; nếu Telegram từ chối entity/HTML, chỉ lỗi parse mới fallback về plain text để không làm mất câu trả lời.

**Tech Stack:** Python 3.11+, aiogram, stdlib `html`, `html.parser.HTMLParser`, `re`, `urllib.parse`; unittest hiện có; ruff hiện có.

**Spec:** Bounded design approved in chat on 2026-09-08; no separate spec file is required for this scoped change.

## Global Constraints

- Không thêm dependency mới.
- Không thay đổi thứ tự provider, model, search backend hoặc tool-calling.
- Nội dung chính dùng Telegram HTML; footer nguồn hiện tại tiếp tục dùng HTML riêng như đang có.
- Cho phép có kiểm soát: `<b>`, `<i>`, `<u>`, `<s>`, `<tg-spoiler>`, `<code>`, `<pre>`, `<blockquote>`, `<blockquote expandable>`, `<a href="https://...">`.
- Không cho model dùng Markdown làm format chính; lớp compatibility chỉ sửa các pattern Markdown phổ biến để tránh raw `**`, heading và code fence lọt ra UI.
- Chỉ cho phép link `http` và `https` trong `<a>`; scheme khác phải bị vô hiệu hóa nhưng vẫn giữ label text.
- Độ dài mỗi phần trả lời tối đa 3900 UTF-16 code units của phần text hiển thị, giữ margin dưới giới hạn Telegram 4096.
- Không làm mất nội dung khi split; ghép plain text của các chunk phải tương đương plain text của output đã sanitize.
- Memory hội thoại lưu plain text, không lưu HTML markup.
- Chỉ fallback plain text khi Telegram trả lỗi parse/entity kiểu `TelegramBadRequest`; không retry mù với lỗi mạng để tránh duplicate message.

---

## File map

- Create: `app/core/telegram_formatting.py` — normalize markup, sanitize Telegram HTML, convert HTML về plain text, split HTML an toàn theo UTF-16.
- Modify: `app/core/orchestrator.py` — đổi system prompt thành Telegram-native formatting contract.
- Modify: `app/bot/handlers.py` — dùng formatter/splitter mới, gửi `parse_mode="HTML"`, fallback plain text cho lỗi parse.
- Create: `tests/test_telegram_formatting.py` — unit tests cho sanitizer, compatibility normalization, plain conversion, splitter.
- Modify: `tests/test_search_policy_prompt.py` — regression tests cho format contract trong system prompt.
- Create: `tests/test_telegram_delivery.py` — integration-style tests cho helper gửi HTML/fallback trong handler.
- Modify: `README.md` — document cách bot format câu trả lời và giới hạn an toàn.

---

### Task 1: Khóa format contract trong system prompt

**Files:**
- Modify: `tests/test_search_policy_prompt.py`
- Modify: `app/core/orchestrator.py` (`Orchestrator.system_prompt`)

**Interfaces:**
- Consumes: `Orchestrator.system_prompt() -> str`
- Produces: prompt contract yêu cầu Telegram HTML, cấm Markdown thô, giới hạn format và giữ policy nguồn hiện tại.

- [ ] **Step 1: Viết test fail cho Telegram formatting contract**

Thêm vào `SearchPolicyPromptTests`:

```python
def test_prompt_requests_telegram_native_html(self):
    self.assertIn("telegram", self.prompt)
    self.assertIn("html", self.prompt)
    for tag in ("<b>", "<i>", "<code>", "<pre>", "<blockquote>"):
        with self.subTest(tag=tag):
            self.assertIn(tag, self.prompt)


def test_prompt_forbids_raw_markdown_and_format_overuse(self):
    self.assertIn("không dùng markdown", self.prompt)
    self.assertIn("không lạm dụng", self.prompt)
    self.assertIn("đoạn ngắn", self.prompt)
    self.assertIn("bullet", self.prompt)


def test_prompt_keeps_sources_out_of_main_answer(self):
    self.assertIn("hệ thống tự đính nguồn", self.prompt)
    self.assertIn("không cần liệt kê nguồn", self.prompt)
```

- [ ] **Step 2: Chạy test và xác nhận fail đúng nguyên nhân**

Run:

```bash
python -m unittest tests.test_search_policy_prompt -v
```

Expected: các test mới fail vì prompt hiện tại nói `không dùng markdown/HTML` và chưa có Telegram HTML contract.

- [ ] **Step 3: Sửa Rule 2 và Rule 9 trong `system_prompt()`**

Thay Rule 2 hiện tại bằng nội dung tương đương sau, giữ nguyên các rule khác:

```python
"2. Trả lời như một tin nhắn Telegram tự nhiên: ngắn gọn, dễ đọc, ưu tiên 1-3 "
"đoạn ngắn cho câu hỏi đơn giản. Được dùng Telegram HTML có chọn lọc để tăng khả năng "
"đọc: <b>, <i>, <u>, <s>, <tg-spoiler>, <code>, <pre>, <blockquote> và "
"<a href=\"https://...\">. Không dùng Markdown làm format (không **bold**, heading #, "
"code fence ```); không lạm dụng format; chỉ dùng bullet khi thật sự có nhiều ý độc lập; "
"không tạo kiểu mini-report với nhiều nhãn nếu user không yêu cầu.\n"
```

Đổi Rule 9 thành:

```python
"9. Hệ thống tự đính nguồn; không cần liệt kê nguồn trong nội dung chính. Chỉ dùng <a> "
"khi link là một phần trực tiếp của câu trả lời user yêu cầu.\n"
```

- [ ] **Step 4: Chạy test lại**

```bash
python -m unittest tests.test_search_policy_prompt -v
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add app/core/orchestrator.py tests/test_search_policy_prompt.py
git commit -m "feat: define Telegram HTML response contract"
```

---

### Task 2: Tạo sanitizer và compatibility normalization

**Files:**
- Create: `tests/test_telegram_formatting.py`
- Create: `app/core/telegram_formatting.py`

**Interfaces:**
- Produces: `sanitize_telegram_html(text: str) -> str`
- Produces: `telegram_html_to_plain(text: str) -> str`
- Internal: `_normalize_legacy_markdown(text: str) -> str`

- [ ] **Step 1: Viết các test fail cho sanitizer**

Tạo `tests/test_telegram_formatting.py` với các test tối thiểu sau:

```python
import unittest

from app.core.telegram_formatting import sanitize_telegram_html, telegram_html_to_plain


class TelegramFormattingTests(unittest.TestCase):
    def test_preserves_supported_telegram_tags(self):
        source = (
            '<b>Đậm</b> <i>Nghiêng</i> <u>Gạch chân</u> <s>Bỏ</s> '
            '<tg-spoiler>ẩn</tg-spoiler> <code>x = 1</code> '
            '<blockquote>trích dẫn</blockquote> '
            '<a href="https://example.com/a?x=1&y=2">link</a>'
        )
        safe = sanitize_telegram_html(source)
        self.assertIn("<b>Đậm</b>", safe)
        self.assertIn("<tg-spoiler>ẩn</tg-spoiler>", safe)
        self.assertIn('<a href="https://example.com/a?x=1&amp;y=2">link</a>', safe)

    def test_drops_unsupported_tags_but_keeps_text(self):
        safe = sanitize_telegram_html("hello <script>alert(1)</script> world")
        self.assertEqual(safe, "hello alert(1) world")

    def test_rejects_unsafe_link_scheme_but_keeps_label(self):
        safe = sanitize_telegram_html('<a href="javascript:alert(1)">click</a>')
        self.assertEqual(safe, "click")

    def test_normalizes_common_markdown_leaks(self):
        source = "### Trạng thái\n- **Singapore:** lỗi\n```text\nraw <tag>\n```"
        safe = sanitize_telegram_html(source)
        self.assertIn("<b>Trạng thái</b>", safe)
        self.assertIn("• <b>Singapore:</b> lỗi", safe)
        self.assertIn("<pre>raw &lt;tag&gt;\n</pre>", safe)
        self.assertNotIn("**", safe)
        self.assertNotIn("```", safe)

    def test_plain_conversion_removes_markup_without_losing_text(self):
        source = "<b>Có</b> <code>/status</code> &amp; <i>ổn</i>"
        self.assertEqual(telegram_html_to_plain(source), "Có /status & ổn")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test và xác nhận fail vì module chưa tồn tại**

```bash
python -m unittest tests.test_telegram_formatting -v
```

Expected: FAIL/ERROR với `ModuleNotFoundError: app.core.telegram_formatting`.

- [ ] **Step 3: Implement normalize + sanitizer bằng stdlib**

Tạo `app/core/telegram_formatting.py` với các nguyên tắc sau:

```python
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

_TAG_ALIASES = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
    "tg-spoiler": "tg-spoiler",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "a": "a",
}

_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+.-]+)?\n(.*?)```", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*([^\n*][^\n]*?)\*\*")
_HEADING_RE = re.compile(r"^(?:#{1,6})\s+(.+)$")


def _normalize_legacy_markdown(text: str) -> str:
    def fence(match: re.Match[str]) -> str:
        body = html.escape(match.group(1), quote=False)
        return f"<pre>{body}</pre>"

    text = _FENCE_RE.sub(fence, text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    lines: list[str] = []
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            lines.append(f"<b>{heading.group(1)}</b>")
        elif line.startswith("- "):
            lines.append("• " + line[2:])
        else:
            lines.append(line)
    return "\n".join(lines)


class _TelegramSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        canonical = _TAG_ALIASES.get(tag.lower())
        if canonical is None:
            return
        if canonical == "a":
            href = dict(attrs).get("href") or ""
            parsed = urlparse(href)
            if parsed.scheme.lower() not in {"http", "https"}:
                return
            self.out.append(f'<a href="{html.escape(href, quote=True)}">')
        elif canonical == "blockquote":
            expandable = any(name.lower() == "expandable" for name, _ in attrs)
            self.out.append("<blockquote expandable>" if expandable else "<blockquote>")
        else:
            self.out.append(f"<{canonical}>")
        self.stack.append(canonical)

    def handle_endtag(self, tag: str) -> None:
        canonical = _TAG_ALIASES.get(tag.lower())
        if canonical is None or canonical not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.out.append(f"</{opened}>")
            if opened == canonical:
                break

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def finish(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_telegram_html(text: str) -> str:
    parser = _TelegramSanitizer()
    parser.feed(_normalize_legacy_markdown(text or ""))
    parser.close()
    return parser.finish()
```

Trong cùng file, implement plain conversion bằng `HTMLParser` riêng để lấy `handle_data()` và thêm newline cho block-level tags (`pre`, `blockquote`) thay vì regex strip tag.

- [ ] **Step 4: Chạy unit tests sanitizer**

```bash
python -m unittest tests.test_telegram_formatting -v
```

Expected: PASS cho sanitizer/normalization/plain conversion.

- [ ] **Step 5: Commit checkpoint**

```bash
git add app/core/telegram_formatting.py tests/test_telegram_formatting.py
git commit -m "feat: sanitize Telegram HTML responses"
```

---

### Task 3: Split Telegram HTML theo visible UTF-16 mà không làm vỡ tag

**Files:**
- Modify: `tests/test_telegram_formatting.py`
- Modify: `app/core/telegram_formatting.py`

**Interfaces:**
- Consumes: `sanitize_telegram_html(text: str) -> str`
- Produces: `split_telegram_html(text: str, limit: int = 3900) -> list[str]`
- Internal: `_utf16_len(text: str) -> int`, `_prefix_fitting(text: str, limit: int) -> int`, `_find_visible_cut(text: str, limit: int) -> int`

- [ ] **Step 1: Viết test fail cho splitter**

Thêm các test:

```python
from app.core.telegram_formatting import split_telegram_html


def _plain(parts: list[str]) -> str:
    return "".join(telegram_html_to_plain(part) for part in parts)


class TelegramSplitTests(unittest.TestCase):
    def test_splits_long_bold_text_and_reopens_tag(self):
        source = "<b>" + ("a" * 5000) + "</b>"
        parts = split_telegram_html(source, limit=3900)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.startswith("<b>") and part.endswith("</b>") for part in parts))
        self.assertEqual(_plain(parts), "a" * 5000)

    def test_splits_preformatted_text_without_breaking_html(self):
        source = "<pre>" + ("log line\n" * 700) + "</pre>"
        parts = split_telegram_html(source, limit=3900)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(part.startswith("<pre>") and part.endswith("</pre>") for part in parts))
        self.assertEqual(_plain(parts), "log line\n" * 700)

    def test_counts_emoji_by_utf16_units(self):
        source = "😀" * 2200
        parts = split_telegram_html(source, limit=3900)
        self.assertEqual(_plain(parts), source)
        for part in parts:
            plain = telegram_html_to_plain(part)
            self.assertLessEqual(len(plain.encode("utf-16-le")) // 2, 3900)

    def test_keeps_anchor_valid_across_split(self):
        source = '<a href="https://example.com">' + ("x" * 5000) + "</a>"
        parts = split_telegram_html(source, limit=3900)
        self.assertEqual(_plain(parts), "x" * 5000)
        self.assertTrue(all(part.startswith('<a href="https://example.com">') for part in parts))
        self.assertTrue(all(part.endswith("</a>") for part in parts))
```

- [ ] **Step 2: Chạy test và xác nhận fail vì `split_telegram_html` chưa có**

```bash
python -m unittest tests.test_telegram_formatting.TelegramSplitTests -v
```

Expected: FAIL/ERROR vì interface chưa được implement.

- [ ] **Step 3: Implement splitter event-based**

Implementation phải sanitize trước, parse HTML đã sanitize thành event `start/end/text`, đếm chỉ phần text decoded, và khi flush chunk phải đóng tạm mọi active tag rồi mở lại ở chunk kế tiếp.

Dùng cấu trúc dữ liệu nội bộ cố định:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _StartTag:
    open_html: str
    close_html: str


@dataclass(frozen=True)
class _Text:
    value: str


@dataclass(frozen=True)
class _EndTag:
    close_html: str
```

`split_telegram_html()` phải thỏa invariants:

```python
def split_telegram_html(text: str, limit: int = 3900) -> list[str]:
    safe = sanitize_telegram_html(text)
    events = _tokenize_safe_html(safe)
    parts: list[str] = []
    current: list[str] = []
    active: list[_StartTag] = []
    visible_len = 0

    def flush() -> None:
        nonlocal current, visible_len
        if visible_len == 0:
            return
        closing = [tag.close_html for tag in reversed(active)]
        parts.append("".join([*current, *closing]))
        current = [tag.open_html for tag in active]
        visible_len = 0

    for event in events:
        if isinstance(event, _StartTag):
            current.append(event.open_html)
            active.append(event)
            continue
        if isinstance(event, _EndTag):
            current.append(event.close_html)
            if active and active[-1].close_html == event.close_html:
                active.pop()
            continue

        remaining_text = event.value
        while remaining_text:
            remaining = limit - visible_len
            if remaining <= 0:
                flush()
                remaining = limit
            cut = _find_visible_cut(remaining_text, remaining)
            if cut <= 0:
                flush()
                continue
            piece = remaining_text[:cut]
            current.append(html.escape(piece, quote=False))
            visible_len += _utf16_len(piece)
            remaining_text = remaining_text[cut:]
            if remaining_text:
                flush()

    if visible_len > 0:
        closing = [tag.close_html for tag in reversed(active)]
        parts.append("".join([*current, *closing]))
    return parts
```

`_find_visible_cut()` kế thừa policy hiện tại: ưu tiên `\n\n`, rồi `\n`, `. `, `? `, `! ` trong khoảng gần limit; nếu không có thì dùng `_prefix_fitting()`.

- [ ] **Step 4: Chạy toàn bộ formatter tests**

```bash
python -m unittest tests.test_telegram_formatting -v
```

Expected: PASS; không chunk nào vượt 3900 UTF-16 visible units; round-trip plain text không mất dữ liệu.

- [ ] **Step 5: Commit checkpoint**

```bash
git add app/core/telegram_formatting.py tests/test_telegram_formatting.py
git commit -m "feat: split Telegram HTML safely"
```

---

### Task 4: Tích hợp Telegram HTML delivery vào handler và fallback parse lỗi

**Files:**
- Create: `tests/test_telegram_delivery.py`
- Modify: `app/bot/handlers.py`

**Interfaces:**
- Consumes: `split_telegram_html(text: str, limit: int = 3900) -> list[str]`
- Consumes: `telegram_html_to_plain(text: str) -> str`
- Produces internal helper: `_send_answer_parts(message: Message, parts: list[str]) -> bool`
- Behavior: first chunk reply vào user message; chunk sau dùng `bot.send_message`; tất cả dùng HTML + disabled link preview; parse failure fallback plain.

- [ ] **Step 1: Viết failing delivery tests với fake objects**

Tạo `tests/test_telegram_delivery.py`:

```python
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers import _send_answer_parts


class TelegramDeliveryTests(unittest.TestCase):
    def test_sends_all_parts_with_html_parse_mode(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(),
            )
            ok = await _send_answer_parts(message, ["<b>Một</b>", "<i>Hai</i>"])
            self.assertTrue(ok)
            self.assertEqual(message.reply.await_args.kwargs["parse_mode"], "HTML")
            self.assertEqual(bot.send_message.await_args.kwargs["parse_mode"], "HTML")
            self.assertTrue(
                message.reply.await_args.kwargs["link_preview_options"].is_disabled
            )

        asyncio.run(run())

    def test_first_part_falls_back_to_plain_on_bad_html(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(
                    side_effect=[
                        TelegramBadRequest(method=SimpleNamespace(), message="can't parse entities"),
                        None,
                    ]
                ),
            )
            ok = await _send_answer_parts(message, ["<b>Hello</b>"])
            self.assertTrue(ok)
            second_call = message.reply.await_args_list[1]
            self.assertEqual(second_call.args[0], "Hello")
            self.assertNotIn("parse_mode", second_call.kwargs)

        asyncio.run(run())
```

- [ ] **Step 2: Chạy test và xác nhận fail vì helper chưa tồn tại**

```bash
python -m unittest tests.test_telegram_delivery -v
```

Expected: FAIL/ERROR import `_send_answer_parts`.

- [ ] **Step 3: Implement `_send_answer_parts()` và wire vào `_handle_question()`**

Trong `app/bot/handlers.py`:

```python
from aiogram.exceptions import TelegramBadRequest

from ..core.telegram_formatting import split_telegram_html, telegram_html_to_plain
```

Thêm helper:

```python
async def _send_answer_parts(message: Message, parts: list[str]) -> bool:
    if not parts:
        return False

    preview = LinkPreviewOptions(is_disabled=True)

    async def reply_part(text: str) -> None:
        try:
            await message.reply(text, parse_mode="HTML", link_preview_options=preview)
        except TelegramBadRequest:
            await message.reply(telegram_html_to_plain(text))

    try:
        await reply_part(parts[0])
    except Exception:
        logger.exception("Gửi câu trả lời đầu tiên thất bại (chat %s)", message.chat.id)
        return False

    extra_kwargs = {}
    if message.message_thread_id:
        extra_kwargs["message_thread_id"] = message.message_thread_id

    for part in parts[1:]:
        try:
            await message.bot.send_message(
                chat_id=message.chat.id,
                text=part,
                parse_mode="HTML",
                link_preview_options=preview,
                **extra_kwargs,
            )
        except TelegramBadRequest:
            await message.bot.send_message(
                chat_id=message.chat.id,
                text=telegram_html_to_plain(part),
                **extra_kwargs,
            )
        await asyncio.sleep(0.15)
    return True
```

Trong `_handle_question()`:

```python
parts = split_telegram_html(answer.text, 3900) or ["..."]
if not await _send_answer_parts(message, parts):
    return
```

Xóa loop gửi text cũ để tránh duplicate. Phần gửi ảnh và source footer giữ nguyên.

Đổi memory assistant thành plain text:

```python
memory.push(chat_id, "assistant", telegram_html_to_plain(answer.text))
```

- [ ] **Step 4: Chạy delivery + formatter tests**

```bash
python -m unittest tests.test_telegram_delivery tests.test_telegram_formatting -v
```

Expected: PASS.

- [ ] **Step 5: Chạy các regression tests liên quan handler hiện có**

```bash
python tests/run_tests.py
```

Expected: PASS; không thay đổi flow ảnh/search/source.

- [ ] **Step 6: Commit checkpoint**

```bash
git add app/bot/handlers.py tests/test_telegram_delivery.py
git commit -m "feat: send answers with Telegram HTML formatting"
```

---

### Task 5: Docs, acceptance cases và full verification

**Files:**
- Modify: `README.md`
- Verify: `.github/workflows/audit.yml` commands locally

**Interfaces:**
- Produces: tài liệu vận hành phản ánh đúng format behavior mới.

- [ ] **Step 1: Update README**

Thêm vào phần mô tả bot/behavior một đoạn ngắn:

```markdown
### Telegram-native formatting

Câu trả lời chính được render bằng Telegram HTML có kiểm soát để hỗ trợ bold, italic,
underline, strikethrough, spoiler, inline code, preformatted blocks, blockquote và link.
Output AI luôn đi qua sanitizer và HTML-aware splitter trước khi gửi; message dài được chia
ở mức 3900 UTF-16 units của phần text nhìn thấy. Nếu Telegram từ chối HTML entities, bot chỉ
fallback phần bị lỗi sang plain text thay vì làm mất cả câu trả lời.
```

- [ ] **Step 2: Chạy acceptance examples bằng unit-level formatter check**

Chạy trong Python shell hoặc test tạm tại local, không commit test tạm:

```python
from app.core.telegram_formatting import split_telegram_html

sample = """Có. <b>Sáng nay 8/9 Telegram có ghi nhận gián đoạn</b> ở một số khu vực.

• <b>Singapore:</b> gần 4.000 báo cáo.
• <b>Ấn Độ:</b> khoảng 784 báo cáo.
• <b>Hiện tại:</b> dịch vụ đang dần ổn định.

<i>Telegram chưa công bố nguyên nhân chính thức.</i>"""

parts = split_telegram_html(sample)
assert len(parts) == 1
assert "**" not in parts[0]
assert "<b>Singapore:</b>" in parts[0]
```

- [ ] **Step 3: Chạy đúng bộ verification của CI**

```bash
python -m ruff check .
python -m compileall -q app tests
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: tất cả PASS, không warning/error mới.

- [ ] **Step 4: Kiểm tra diff chỉ nằm trong scope**

```bash
git status --short
git diff --stat main...HEAD
git diff main...HEAD -- app/core/orchestrator.py app/core/telegram_formatting.py app/bot/handlers.py tests README.md
```

Expected files:

```text
README.md
app/bot/handlers.py
app/core/orchestrator.py
app/core/telegram_formatting.py
tests/test_search_policy_prompt.py
tests/test_telegram_delivery.py
tests/test_telegram_formatting.py
```

Không được có thay đổi model/provider/search/config ngoài scope.

- [ ] **Step 5: Commit docs**

```bash
git add README.md
git commit -m "docs: document Telegram native formatting"
```

- [ ] **Step 6: Final verification trước PR**

```bash
python -m ruff check . && \
python -m compileall -q app tests && \
python tests/run_tests.py && \
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: exit code 0.

---

## Acceptance criteria

1. Câu trả lời dạng `**Vùng ảnh hưởng:**` không còn xuất hiện raw Markdown trong Telegram; model được yêu cầu dùng `<b>Vùng ảnh hưởng:</b>` và compatibility layer xử lý trường hợp provider vẫn leak `**...**`.
2. Câu trả lời ngắn không bị ép thành mini-report; format chỉ dùng khi cải thiện khả năng đọc.
3. `<b>`, `<i>`, `<u>`, `<s>`, spoiler, code/pre, blockquote và link HTTPS hiển thị native trong Telegram.
4. Unsupported HTML hoặc unsafe link scheme không thể làm Telegram reject message; text label vẫn còn.
5. Message >3900 UTF-16 visible units được chia thành nhiều message hợp lệ mà không vỡ tag hoặc mất nội dung.
6. Emoji/surrogate pair không bị cắt giữa code units.
7. Source footer HTML hiện có tiếp tục hoạt động độc lập.
8. Conversation memory không chứa HTML markup.
9. Nếu Telegram trả `TelegramBadRequest` do parse/entities, bot retry đúng phần đó bằng plain text; lỗi mạng không bị retry mù.
10. Full CI-equivalent suite pass trên Python codebase hiện tại.

## Recommended execution order

1. Task 1 — prompt contract.
2. Task 2 — sanitizer.
3. Task 3 — HTML-aware splitter.
4. Task 4 — handler delivery.
5. Task 5 — docs + full verification + PR.

Không merge hoặc tạo PR trước khi Task 5 exit code 0.