"""Token-usage propagation from the provider payload to the answer.

The pilot cannot be judged on cost while the adapters drop the ``usage`` object,
so the OpenAI-compatible driver now parses it, the router sums it over the whole
request (tool rounds and rotations each cost tokens), and the orchestrator
surfaces it on the answer plus one log line for monitoring. A provider that
reports nothing must yield ``None`` — never zero, which would read as a real
measurement.
"""

from __future__ import annotations

import logging
import unittest

from app.ai.contracts import ChatResponse
from app.ai.router import AIProviderRouter, CompletionResult
from app.ai.drivers.openai_chat import parse_response
from app.config import Settings
from app.core.orchestrator import Orchestrator
from scripts.web_evidence_live_smoke import _usage_medians
from tests.provider_fakes import (
    ScriptedProvider,
    fetch_url_definition,
    text_request,
    tool_response,
)

USAGE = {"prompt_tokens": 41, "completion_tokens": 7, "total_tokens": 48}


def _payload(usage: object = None, *, with_usage: bool = True) -> dict:
    data: dict = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    }
    if with_usage:
        data["usage"] = usage
    return data


class DriverUsageParsingTests(unittest.TestCase):
    def test_known_counters_are_kept_and_extras_dropped(self):
        response = parse_response(
            _payload({**USAGE, "cached_tokens": 12, "model": "x"}), (), "test"
        )
        self.assertEqual(dict(response.usage or {}), USAGE)
        self.assertNotIn("cached_tokens", response.usage or {})

    def test_missing_or_unusable_payload_stays_none(self):
        for usage, with_usage in (
            (None, False),
            (None, True),
            ({"prompt_tokens": "many", "total_tokens": -3}, True),
            ({"prompt_tokens": True}, True),
            ("48", True),
            ([48], True),
        ):
            with self.subTest(usage=usage):
                response = parse_response(_payload(usage, with_usage=with_usage), (), "test")
                self.assertIsNone(response.usage)

    def test_partial_payload_keeps_only_what_is_usable(self):
        response = parse_response(_payload({"completion_tokens": 0}), (), "test")
        self.assertEqual(dict(response.usage or {}), {"completion_tokens": 0})

    def test_usage_mapping_is_read_only(self):
        response = parse_response(_payload(USAGE), (), "test")
        with self.assertRaises(TypeError):
            response.usage["prompt_tokens"] = 1  # type: ignore[index]


class RouterUsageAggregationTests(unittest.IsolatedAsyncioTestCase):
    async def _complete(self, usage: dict | None) -> CompletionResult:
        provider = ScriptedProvider(
            "xkiro",
            [tool_response(1), ChatResponse(content="done", usage=usage)],
        )
        provider.supports_tools = True
        router = AIProviderRouter([provider], max_tool_rounds=1)
        try:
            return await router.complete(
                text_request("solar capacity", tools=(fetch_url_definition(),)),
                lambda _name, _args: _unused(),
            )
        finally:
            await provider.aclose()

    async def test_final_response_usage_reaches_the_result(self):
        result = await self._complete(dict(USAGE))
        self.assertEqual(dict(result.usage or {}), USAGE)

    async def test_provider_without_usage_reports_none(self):
        result = await self._complete(None)
        self.assertIsNone(result.usage)


async def _unused() -> str:
    return "[SOURCE 1 / CHUNK 1] URL: https://example.com/x\nsolar capacity 731"


class RouterMultiRoundUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_round_tokens_are_added_to_the_final_response(self):
        provider = ScriptedProvider(
            "xkiro",
            [
                ChatResponse(
                    tool_calls=tool_response(1).tool_calls,
                    usage={"prompt_tokens": 10, "completion_tokens": 2},
                ),
                ChatResponse(content="done", usage={"prompt_tokens": 30, "completion_tokens": 5}),
            ],
        )
        provider.supports_tools = True
        router = AIProviderRouter([provider], max_tool_rounds=1)
        try:
            result = await router.complete(
                text_request("solar capacity", tools=(fetch_url_definition(),)),
                lambda _name, _args: _unused(),
            )
        finally:
            await provider.aclose()
        self.assertEqual(dict(result.usage or {}), {"prompt_tokens": 40, "completion_tokens": 7})


class _UsageRouter:
    def __init__(self, usage: dict | None) -> None:
        self.usage = usage
        self.router = None

    async def complete(self, request, tool_executor, **kwargs):
        return CompletionResult("Trả lời ngắn.", "fake", (), usage=self.usage)


class OrchestratorUsageTests(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, usage: dict | None) -> tuple:
        settings = Settings(_env_file=None, search_backend="ddgs")
        orchestrator = Orchestrator(settings, _UsageRouter(usage))
        logger = logging.getLogger("app.core.orchestrator")
        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = _Capture()
        level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            answer = await orchestrator.ask("Tóm tắt giúp mình")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        return answer, records

    async def test_answer_carries_usage_and_one_log_line(self):
        answer, records = await self._ask(dict(USAGE))
        self.assertEqual(dict(answer.usage or {}), USAGE)
        lines = [line for line in records if "ai_usage" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("prompt=41", lines[0])
        self.assertIn("completion=7", lines[0])
        self.assertIn("total=48", lines[0])

    async def test_answer_without_provider_usage_is_none_and_silent(self):
        answer, records = await self._ask(None)
        self.assertIsNone(answer.usage)
        self.assertFalse([line for line in records if "ai_usage" in line])


class SmokeUsageSummaryTests(unittest.TestCase):
    def test_medians_ignore_rows_without_a_usage_payload(self):
        rows = [
            {"provider_token_usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            {"provider_token_usage": {"prompt_tokens": 20, "completion_tokens": 4}},
            {"provider_token_usage": "NOT REPORTED BY PROVIDER"},
            {"status": "timeout"},
        ]
        self.assertEqual(_usage_medians(rows, "prompt_tokens"), 15)
        self.assertEqual(_usage_medians(rows, "completion_tokens"), 3)

    def test_medians_are_none_when_nothing_reported_usage(self):
        self.assertIsNone(_usage_medians([{"status": "error"}], "prompt_tokens"))


if __name__ == "__main__":
    unittest.main()
