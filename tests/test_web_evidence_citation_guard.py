"""Deterministic V2 citation guard: unit contract plus Orchestrator wiring.

The guard is the code-level enforcement of the V2 grounding rule. These tests
pin the two directions that matter: a citation backed by the evidence the model
actually received must survive untouched, and any other URL-shaped citation must
lose its hyperlink and be marked as unverified, without breaking Telegram HTML or
Markdown, and without failing open.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.ai.router import CompletionResult
from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.search import citation_guard
from app.search.citation_guard import (
    UNVERIFIED_MARKER,
    CitationDiagnostics,
    enforce_v2_citations,
)
from app.search.url_service import UrlReadResult

QUERY = "solar capacity 2026"
EVIDENCE_URL = "https://www.nasa.gov/mission/artemis-iii"
EVIDENCE_HIT = {
    "title": "Artemis III mission page",
    "url": EVIDENCE_URL,
    "snippet": "Crewed flight in low Earth orbit.",
}
INVENTED_URL = "https://www.nasa.gov/mission/artemis-iv"


class CitationGuardUnitTests(unittest.TestCase):
    def test_citation_present_in_evidence_is_kept_verbatim(self):
        text = f"Theo [NASA]({EVIDENCE_URL}) thì đây là chuyến bay có phi hành đoàn."
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertEqual(guarded, text)
        self.assertNotIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(
            (diagnostics.detected, diagnostics.allowed, diagnostics.removed),
            (1, 1, 0),
        )
        self.assertEqual(dict(diagnostics.reasons), {})

    def test_citation_that_ends_a_sentence_keeps_its_evidence_link(self):
        text = f"Chi tiết ở {EVIDENCE_URL}."
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertEqual(guarded, text)
        self.assertEqual(diagnostics.allowed, 1)
        self.assertEqual(diagnostics.removed, 0)

    def test_marked_bare_url_keeps_its_sentence_punctuation(self):
        guarded, diagnostics = enforce_v2_citations(
            "Xem https://invented.example.org/x.", set()
        )
        self.assertEqual(guarded, f"Xem {UNVERIFIED_MARKER}.")
        self.assertEqual(diagnostics.removed, 1)

    def test_markdown_citation_outside_evidence_is_unlinked_and_marked(self):
        text = f"Theo [NASA]({INVENTED_URL}) thì hạ cánh có người là 2028."
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertNotIn(INVENTED_URL, guarded)
        self.assertNotIn("](http", guarded)
        self.assertIn("NASA", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)
        self.assertEqual(diagnostics.allowed, 0)
        self.assertEqual(diagnostics.reasons["not_in_evidence"], 1)

    def test_telegram_html_anchor_is_unlinked_without_breaking_formatting(self):
        text = (
            "<b>Kết luận</b>: "
            "<a href=\"https://invented.example.org/x\">trang chính thức</a>"
        )
        guarded, diagnostics = enforce_v2_citations(text, set())
        self.assertNotIn("<a href", guarded)
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn("<b>Kết luận</b>", guarded)
        self.assertIn("trang chính thức", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)

    def test_bare_url_outside_evidence_is_removed_and_marked(self):
        text = "Nguồn: https://invented.example.org/x (chưa chắc chắn)."
        guarded, diagnostics = enforce_v2_citations(text, set())
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)

    def test_representation_differences_normalize_to_the_same_evidence_url(self):
        allowed = {"https://Example.ORG:443/report/?utm_source=newsletter#top"}
        for spelling in (
            "https://example.org/report",
            "https://example.org/report/",
            "HTTPS://EXAMPLE.ORG:443/report",
        ):
            with self.subTest(spelling=spelling):
                text = f"Xem [báo cáo]({spelling})."
                guarded, diagnostics = enforce_v2_citations(text, allowed)
                self.assertEqual(guarded, text)
                self.assertEqual(diagnostics.allowed, 1)
                self.assertEqual(diagnostics.removed, 0)

    def test_confusing_hostnames_are_not_accepted_as_evidence(self):
        cases = (
            "https://www.nasa.gov.evil.example.net/mission/artemis-iii",
            "https://evil.example.net/?next=https://www.nasa.gov/mission/artemis-iii",
            "https://evil.example.net/www.nasa.gov/mission/artemis-iii",
        )
        for url in cases:
            with self.subTest(url=url):
                guarded, diagnostics = enforce_v2_citations(
                    f"Xem [nguồn]({url}).", {EVIDENCE_URL}
                )
                self.assertNotIn(url, guarded)
                self.assertIn("nguồn", guarded)
                self.assertIn(UNVERIFIED_MARKER, guarded)
                self.assertEqual(diagnostics.removed, 1)
                self.assertEqual(diagnostics.allowed, 0)

    def test_no_evidence_means_no_citation_survives(self):
        text = (
            "A https://a.example.org/1 "
            "B [x](https://b.example.org/2) "
            'C <a href="https://c.example.org/3">y</a>'
        )
        guarded, diagnostics = enforce_v2_citations(text, set())
        for url in (
            "https://a.example.org/1",
            "https://b.example.org/2",
            "https://c.example.org/3",
        ):
            self.assertNotIn(url, guarded)
        self.assertEqual(
            (diagnostics.detected, diagnostics.allowed, diagnostics.removed),
            (3, 0, 3),
        )
        self.assertEqual(guarded.count(UNVERIFIED_MARKER), 3)

    def test_url_without_a_parseable_host_is_removed_not_trusted(self):
        guarded, diagnostics = enforce_v2_citations(
            "Xem https:///no-host/path nhé.", set()
        )
        self.assertNotIn("no-host", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.reasons["unparseable_url"], 1)

    def test_guard_is_idempotent(self):
        text = "Xem [nguồn](https://invented.example.org/x)."
        first, _ = enforce_v2_citations(text, set())
        second, diagnostics = enforce_v2_citations(first, set())
        self.assertEqual(second, first)
        self.assertEqual(diagnostics.detected, 0)

    def test_guard_error_fails_closed(self):
        """A broken guard must mark citations, never wave them through."""
        with patch.object(citation_guard, "_enforce", side_effect=RuntimeError("boom")):
            guarded, diagnostics = enforce_v2_citations(
                f"Xem {INVENTED_URL} và {EVIDENCE_URL} nhé.", {EVIDENCE_URL}
            )
        self.assertNotIn(INVENTED_URL, guarded)
        self.assertIn(EVIDENCE_URL, guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.error, "RuntimeError")
        self.assertEqual(diagnostics.removed, 1)

    def test_emergency_pass_catches_uppercase_https(self):
        for text, url in (
            ("Nguồn: HTTPS://evil.example.org/upper", "HTTPS://evil.example.org/upper"),
            ("İ Nguồn: HtTpS://evil.example.org/mixed", "HtTpS://evil.example.org/mixed"),
        ):
            with self.subTest(url=url):
                with patch.object(citation_guard, "_enforce", side_effect=RuntimeError("boom")):
                    guarded, diagnostics = enforce_v2_citations(text, set())
                self.assertNotIn(url, guarded)
                self.assertEqual(guarded, text.replace(url, UNVERIFIED_MARKER))
                self.assertIn(UNVERIFIED_MARKER, guarded)
                self.assertEqual(diagnostics.removed, 1)
                self.assertEqual(diagnostics.error, "RuntimeError")

    def test_invalid_unicode_url_fails_closed_without_raising(self):
        url = "https://evil.example.org/?q=\ud800"
        guarded, diagnostics = enforce_v2_citations(f"[nguồn]({url})", set())
        self.assertNotIn("evil.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)
        self.assertEqual(diagnostics.error, "UnicodeEncodeError")

    def test_hostile_allowed_element_cannot_escape_the_fail_closed_pass(self):
        """Even a broken allowed set must not let a citation through unmarked."""

        class Hostile:
            def __str__(self):
                raise RuntimeError("hostile allowed element")

            def __bool__(self):
                return True

        guarded, diagnostics = enforce_v2_citations(
            f"Xem {INVENTED_URL} và {EVIDENCE_URL} nhé.", [Hostile()]
        )
        self.assertNotIn(INVENTED_URL, guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.error, "RuntimeError")
        self.assertEqual(diagnostics.allowed, 0)
        self.assertGreaterEqual(diagnostics.removed, 1)

    # --- Phase 1 hardening: render-mechanism parity with the sanitizer ---
    # Answer text is delivered through sanitize_telegram_html, which decodes
    # character references before Telegram sees them, so an entity-encoded
    # href decodes into a real hyperlink. The guard must judge the same
    # decoded text a renderer would produce.

    def test_entity_encoded_html_href_is_unlinked_and_marked(self):
        text = '<a href="&#104;ttps://evil.example/path">nguồn</a>'
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertNotIn("&#104;", guarded)
        self.assertNotIn("evil.example", guarded)
        self.assertNotIn("<a ", guarded)
        self.assertIn("nguồn", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)
        self.assertEqual(diagnostics.allowed, 0)

    def test_entity_encoded_href_that_decodes_to_evidence_is_kept(self):
        text = '<a href="ht&#116;ps://www.nasa.gov/mission/artemis-iii">NASA</a>'
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertEqual(guarded, text)
        self.assertEqual((diagnostics.detected, diagnostics.allowed), (1, 1))
        self.assertEqual(diagnostics.removed, 0)

    def test_entity_encoded_bare_text_url_is_marked(self):
        for text in (
            "&#104;ttps://evil.example/path",
            "h&#116;tps://evil.example/path",  # h + encoded t + t + p + s
            "https://e&#118;il.example/path",
        ):
            with self.subTest(text=text):
                guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
                self.assertNotIn("evil", guarded)
                self.assertNotIn("&#", guarded)
                self.assertIn(UNVERIFIED_MARKER, guarded)
                self.assertEqual(diagnostics.removed, 1)
                self.assertEqual(diagnostics.error, "")

    def test_punycode_url_outside_evidence_is_marked(self):
        guarded, diagnostics = enforce_v2_citations(
            "Xem [nguồn](https://xn--e1afmkfd.xn--p1ai/path).", {EVIDENCE_URL}
        )
        self.assertNotIn("xn--", guarded)
        self.assertIn("nguồn", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)

    def test_unquoted_html_href_is_removed_without_broken_markup(self):
        text = "<a href=https://evil.example/path>source</a>"
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertNotIn("evil.example", guarded)
        self.assertNotIn("href=", guarded)
        self.assertNotIn("<a ", guarded)
        self.assertIn("source", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)

    def test_markdown_angle_autolink_is_marked_as_plain_text(self):
        text = "<https://evil.example/path>"
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertEqual(guarded, UNVERIFIED_MARKER)
        self.assertEqual(diagnostics.removed, 1)

    def test_entity_hardening_is_idempotent(self):
        text = '<a href="&#104;ttps://evil.example/path">nguồn</a>'
        first, _ = enforce_v2_citations(text, {EVIDENCE_URL})
        second, diagnostics = enforce_v2_citations(first, {EVIDENCE_URL})
        self.assertEqual(second, first)
        self.assertEqual(diagnostics.detected, 0)

    def test_non_http_anchor_href_is_left_to_the_renderer_policy(self):
        text = '<a href="mailto:x@y.example">mail</a>'
        guarded, _ = enforce_v2_citations(text, set())
        self.assertEqual(guarded, text)

    # --- Group C: spellings Telegram does not turn into hyperlinks ---
    # Telegram only converts http://, https:// (and tg://) text into link
    # entities. The following are documented pass-throughs with the render
    # policy as rationale: they stay plain text and cannot become clickable.

    def test_schemeless_and_protocol_relative_urls_stay_plain_text(self):
        for text in (
            "xem evil.example/path nhé",
            "xem //evil.example/path nhé",
            "xem https:\u200b//evil.example/path nhé",
        ):
            with self.subTest(text=text):
                guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
                self.assertEqual(diagnostics.removed, 0)
                self.assertNotIn(UNVERIFIED_MARKER, guarded)

    def test_diagnostics_are_serializable(self):
        _, diagnostics = enforce_v2_citations("https://x.example.org/1", set())
        self.assertIsInstance(diagnostics, CitationDiagnostics)
        self.assertEqual(
            diagnostics.as_dict(),
            {
                "detected": 1,
                "allowed": 0,
                "removed": 1,
                "reasons": {"not_in_evidence": 1},
                "error": "",
            },
        )


class _SearchThenAnswer:
    """Calls ``web_search`` once, then answers with a fixed text."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.output = ""

    async def complete(self, request, tool_executor, **kwargs):
        self.output = await tool_executor("web_search", {"query": QUERY})
        return CompletionResult(self.answer, "fake", ())


class _FetchThenAnswer:
    """Reads one URL through ``fetch_url``, then answers with a fixed text."""

    def __init__(self, url: str, answer: str) -> None:
        self.url = url
        self.answer = answer
        self.output = ""

    async def complete(self, request, tool_executor, **kwargs):
        self.output = await tool_executor("fetch_url", {"url": self.url})
        return CompletionResult(self.answer, "fake", ())


class OrchestratorCitationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_unicode_citation_does_not_abort_v2_answer(self):
        answer = "Xem [nguồn](https://evil.example.org/?q=\ud800)."
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )
        orchestrator = Orchestrator(settings, _SearchThenAnswer(answer))
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(EVIDENCE_HIT)]),
        ):
            result = await orchestrator.ask("Tóm tắt dữ liệu")
        self.assertIn(UNVERIFIED_MARKER, result.text)
        self.assertNotIn("evil.example.org", result.text)
        self.assertEqual(result.citation_diagnostics.removed, 1)

    async def test_flag_on_marks_a_citation_that_was_never_retrieved(self):
        answer = (
            f"Tổng quan: <a href=\"{EVIDENCE_URL}\">NASA</a> "
            f"và [chi tiết]({INVENTED_URL})."
        )
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )
        orchestrator = Orchestrator(settings, _SearchThenAnswer(answer))
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(EVIDENCE_HIT)]),
        ):
            result = await orchestrator.ask("Sứ mệnh Artemis hạ cánh có người?")

        self.assertIn(EVIDENCE_URL, result.text)
        self.assertNotIn(INVENTED_URL, result.text)
        self.assertIn(UNVERIFIED_MARKER, result.text)
        self.assertIsNotNone(result.citation_diagnostics)
        self.assertEqual(result.citation_diagnostics.detected, 2)
        self.assertEqual(result.citation_diagnostics.allowed, 1)
        self.assertEqual(result.citation_diagnostics.removed, 1)

    async def test_flag_off_leaves_the_answer_byte_for_byte(self):
        answer = f"Theo [NASA]({INVENTED_URL}) thì hạ cánh có người là 2028."
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=False
        )
        orchestrator = Orchestrator(settings, _SearchThenAnswer(answer))
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(EVIDENCE_HIT)]),
        ):
            result = await orchestrator.ask("Sứ mệnh Artemis hạ cánh có người?")

        self.assertEqual(result.text, answer)
        self.assertIsNone(result.citation_diagnostics)

    def test_nested_anchor_inside_evidence_anchor_is_unlinked_and_marked(self):
        text = (
            f'<a href="{EVIDENCE_URL}">chính thức '
            '<a href="https://invented.example.org/x">x</a></a>'
        )
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertIn(f'<a href="{EVIDENCE_URL}">', guarded)
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(
            (diagnostics.detected, diagnostics.allowed, diagnostics.removed),
            (2, 1, 1),
        )

    def test_second_url_inside_evidence_anchor_label_gets_marked(self):
        text = f'<a href="{EVIDENCE_URL}">xem thêm https://invented.example.org/x</a>'
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertIn(f'<a href="{EVIDENCE_URL}">', guarded)
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertEqual(diagnostics.removed, 1)
        self.assertEqual(diagnostics.allowed, 1)

    def test_nested_markdown_link_inside_evidence_anchor_label_gets_marked(self):
        text = (
            f'<a href="{EVIDENCE_URL}">t'
            '[evil](https://invented.example.org/x)</a>'
        )
        guarded, diagnostics = enforce_v2_citations(text, {EVIDENCE_URL})
        self.assertIn(f'<a href="{EVIDENCE_URL}">', guarded)
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)

    def test_nested_deep_inside_allowed_anchor_is_still_marked(self):
        text = (
            f'<a href="{EVIDENCE_URL}">a '
            '<a href="https://allowed-id.example/ok">b <a href="https://invented.example.org/deep">c</a></a></a>'
        )
        allowed = {EVIDENCE_URL, "https://allowed-id.example/ok"}
        guarded, diagnostics = enforce_v2_citations(text, allowed)
        self.assertNotIn("invented.example.org", guarded)
        self.assertIn(UNVERIFIED_MARKER, guarded)
        self.assertIn("https://allowed-id.example/ok", guarded)

    async def test_fetched_page_is_valid_evidence_for_its_own_url(self):
        answer = f"Trang này nói rõ điều đó: {EVIDENCE_URL}"
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )
        orchestrator = Orchestrator(settings, _FetchThenAnswer(EVIDENCE_URL, answer))
        with patch(
            "app.core.orchestrator.url_service.read_url",
            new=AsyncMock(
                return_value=UrlReadResult("body text", EVIDENCE_URL, "generic_reader", True)
            ),
        ):
            result = await orchestrator.ask(f"Đọc giúp {EVIDENCE_URL}")

        self.assertIn(EVIDENCE_URL, result.text)
        self.assertNotIn(UNVERIFIED_MARKER, result.text)
        self.assertEqual(result.citation_diagnostics.removed, 0)

    async def test_user_supplied_url_is_input_not_an_invented_citation(self):
        user_url = "https://example.com/private-doc"
        answer = f"Mình đã đọc {user_url}."
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )
        orchestrator = Orchestrator(settings, _SearchThenAnswer(answer))
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(EVIDENCE_HIT)]),
        ):
            result = await orchestrator.ask(f"Tóm tắt giúp mình {user_url}")

        self.assertIn(user_url, result.text)
        self.assertNotIn(UNVERIFIED_MARKER, result.text)

    async def test_guard_run_is_logged_with_counts(self):
        """Canary observability: every V2 guard run leaves one log line."""
        answer = (
            f"Tổng quan: <a href=\"{EVIDENCE_URL}\">NASA</a> "
            f"và [chi tiết]({INVENTED_URL})."
        )
        settings = Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )
        orchestrator = Orchestrator(settings, _SearchThenAnswer(answer))
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(EVIDENCE_HIT)]),
        ):
            with self.assertLogs("app.core.orchestrator", level="INFO") as captured:
                result = await orchestrator.ask("Sứ mệnh Artemis hạ cánh có người?")
        self.assertEqual(result.citation_diagnostics.detected, 2)
        self.assertEqual(result.citation_diagnostics.removed, 1)
        line = "\n".join(captured.output)
        self.assertIn("v2_citation_guard", line)
        self.assertIn("detected=2", line)
        self.assertIn("allowed=1", line)
        self.assertIn("removed=1", line)


if __name__ == "__main__":
    unittest.main()
