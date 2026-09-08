import unittest

from app.core.telegram_formatting import (
    sanitize_telegram_html,
    split_telegram_html,
    telegram_html_to_plain,
)


def _plain(parts: list[str]) -> str:
    return "".join(telegram_html_to_plain(part) for part in parts)


class TelegramFormattingTests(unittest.TestCase):
    def test_preserves_supported_telegram_tags(self):
        source = (
            '<b>Đậm</b> <i>Nghiêng</i> <u>Gạch chân</u> <s>Bỏ</s> '
            '<tg-spoiler>ẩn</tg-spoiler> <code>x = 1</code> '
            '<blockquote>trích dẫn</blockquote> '
            '<blockquote expandable>chi tiết</blockquote> '
            '<a href="https://example.com/a?x=1&y=2">link</a>'
        )
        safe = sanitize_telegram_html(source)
        self.assertIn("<b>Đậm</b>", safe)
        self.assertIn("<tg-spoiler>ẩn</tg-spoiler>", safe)
        self.assertIn("<blockquote expandable>chi tiết</blockquote>", safe)
        self.assertIn(
            '<a href="https://example.com/a?x=1&amp;y=2">link</a>',
            safe,
        )

    def test_normalizes_alias_tags(self):
        safe = sanitize_telegram_html(
            "<strong>A</strong><em>B</em><ins>C</ins><del>D</del>"
        )
        self.assertEqual(safe, "<b>A</b><i>B</i><u>C</u><s>D</s>")

    def test_drops_unsupported_tags_but_keeps_text(self):
        safe = sanitize_telegram_html("hello <script>alert(1)</script> world")
        self.assertEqual(safe, "hello alert(1) world")

    def test_rejects_unsafe_or_relative_link_but_keeps_label(self):
        bad_links = (
            "javascript:alert(1)",
            "tg://user?id=1",
            "/relative",
            "https:broken",
        )
        for href in bad_links:
            with self.subTest(href=href):
                safe = sanitize_telegram_html(f'<a href="{href}">click</a>')
                self.assertEqual(safe, "click")

    def test_normalizes_common_markdown_leaks(self):
        source = "### Trạng thái\n- **Singapore:** lỗi\n```text\nraw <tag>\n```"
        safe = sanitize_telegram_html(source)
        self.assertIn("<b>Trạng thái</b>", safe)
        self.assertIn("• <b>Singapore:</b> lỗi", safe)
        self.assertIn("<pre>raw &lt;tag&gt;\n</pre>", safe)
        self.assertNotIn("**", safe)
        self.assertNotIn("```", safe)

    def test_does_not_format_markdown_inside_fenced_code(self):
        safe = sanitize_telegram_html("```text\n**literal** <x>\n```")
        self.assertEqual(safe, "<pre>**literal** &lt;x&gt;\n</pre>")

    def test_does_not_format_markdown_inside_existing_code_tag(self):
        safe = sanitize_telegram_html("<code>**literal**</code> ngoài **đậm**")
        self.assertEqual(safe, "<code>**literal**</code> ngoài <b>đậm</b>")

    def test_mixed_existing_code_and_fenced_code_stay_literal(self):
        source = "<code>**inline**</code>\n```text\n**block**\n```\n**outside**"
        safe = sanitize_telegram_html(source)
        self.assertEqual(
            safe,
            "<code>**inline**</code>\n<pre>**block**\n</pre>\n<b>outside</b>",
        )

    def test_escapes_plain_ampersand(self):
        self.assertEqual(sanitize_telegram_html("A & B"), "A &amp; B")

    def test_balances_misnested_supported_tags(self):
        safe = sanitize_telegram_html("<b>A<i>B</b>C</i>")
        self.assertEqual(safe, "<b>A<i>B</i></b>C")

    def test_plain_conversion_removes_markup_without_losing_text(self):
        source = "<b>Có</b> <code>/status</code> &amp; <i>ổn</i>"
        self.assertEqual(telegram_html_to_plain(source), "Có /status & ổn")


class TelegramSplitTests(unittest.TestCase):
    def test_splits_long_bold_text_and_reopens_tag(self):
        source = "<b>" + ("a" * 5000) + "</b>"
        parts = split_telegram_html(source, limit=3900)
        self.assertEqual(len(parts), 2)
        self.assertTrue(
            all(
                part.startswith("<b>") and part.endswith("</b>")
                for part in parts
            )
        )
        self.assertEqual(_plain(parts), "a" * 5000)

    def test_splits_preformatted_text_without_breaking_html(self):
        source = "<pre>" + ("log line\n" * 700) + "</pre>"
        parts = split_telegram_html(source, limit=3900)
        self.assertGreater(len(parts), 1)
        self.assertTrue(
            all(
                part.startswith("<pre>") and part.endswith("</pre>")
                for part in parts
            )
        )
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
        self.assertTrue(
            all(
                part.startswith('<a href="https://example.com">')
                for part in parts
            )
        )
        self.assertTrue(all(part.endswith("</a>") for part in parts))

    def test_nested_tags_remain_valid_and_plain_text_is_lossless(self):
        source = "<blockquote><b>" + ("câu dài. " * 900) + "</b></blockquote>"
        parts = split_telegram_html(source, limit=3900)
        self.assertGreater(len(parts), 1)
        expected = telegram_html_to_plain(sanitize_telegram_html(source))
        self.assertEqual(_plain(parts), expected)
        for part in parts:
            self.assertEqual(sanitize_telegram_html(part), part)
            self.assertLessEqual(
                len(telegram_html_to_plain(part).encode("utf-16-le")) // 2,
                3900,
            )

    def test_empty_input_returns_no_parts(self):
        self.assertEqual(split_telegram_html(""), [])


if __name__ == "__main__":
    unittest.main()
