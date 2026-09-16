"""Regression tests for semantic xKiro vision qualification."""

from __future__ import annotations

import unittest

import httpx

import scripts.probe_xkiro as probe


class XKiroVisionSemanticGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_vision_probe_rejects_response_without_expected_marker(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "I can see an image but not the verification code.",
                            }
                        }
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with self.assertRaisesRegex(ValueError, "expected image marker"):
                await probe._probe_plain(
                    client,
                    "vision-model",
                    max_retries=0,
                    retry_base_delay=0,
                    max_retry_after=probe.DEFAULT_MAX_RETRY_AFTER,
                    messages=[probe.vision_message("data:image/png;base64,AAAA")],
                    event="vision_chat",
                    expected_content="47-GREEN-CIRCLE",
                )

    async def test_vision_probe_accepts_expected_marker_case_insensitively(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "47-green-circle",
                            }
                        }
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            await probe._probe_plain(
                client,
                "vision-model",
                max_retries=0,
                retry_base_delay=0,
                max_retry_after=probe.DEFAULT_MAX_RETRY_AFTER,
                messages=[probe.vision_message("data:image/png;base64,AAAA")],
                event="vision_chat",
                expected_content="47-GREEN-CIRCLE",
            )


if __name__ == "__main__":
    unittest.main()
