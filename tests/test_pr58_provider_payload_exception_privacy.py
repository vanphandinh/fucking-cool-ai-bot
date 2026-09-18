"""PR #58 regressions: provider payloads must not survive in exception chains."""

from __future__ import annotations

import unittest

import httpx

from app.ai.base import ProviderError
from tests.openai_target_fakes import make_catalog_target
from tests.provider_fakes import fetch_url_definition, text_request


class ProviderPayloadExceptionPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_json_response_does_not_retain_raw_document(self) -> None:
        payload_secret = "PR58-INVALID-JSON-PAYLOAD"

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=f"not-json-{payload_secret}",
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="privacy-model",
            credential="test-key",
            target_id="chainnode:text:privacy-json:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        error = raised.exception
        self.assertNotIn(payload_secret, str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    async def test_malformed_tool_arguments_do_not_retain_raw_document(self) -> None:
        payload_secret = "PR58-TOOL-ARGUMENT-PAYLOAD"

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "fetch_url",
                                            "arguments": (
                                                '{"url":"https://example.com",'
                                                f'"token":"{payload_secret}"'
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="privacy-model",
            credential="test-key",
            target_id="chainnode:text:privacy-tools:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        request = text_request(
            "use tool",
            tools=(fetch_url_definition(),),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(request)
        finally:
            await provider.aclose()

        error = raised.exception
        self.assertNotIn(payload_secret, str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)


    async def test_missing_choices_does_not_retain_exception_chain(self) -> None:
        payload_secret = "PR58-MALFORMED-CHOICES-PAYLOAD"

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"diagnostic": payload_secret},
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="privacy-model",
            credential="test-key",
            target_id="chainnode:text:privacy-choices:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        error = raised.exception
        self.assertNotIn(payload_secret, str(error))
        self.assertIsNone(
            error.__cause__,
            "sanitized structural parse errors must not retain the raw parse exception",
        )
        self.assertIsNone(
            error.__context__,
            "sanitized structural parse errors must not retain the raw parse exception",
        )


if __name__ == "__main__":
    unittest.main()
