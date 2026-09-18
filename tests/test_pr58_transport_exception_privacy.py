"""PR #58 regression: provider transport errors must not retain raw credentials."""

from __future__ import annotations

import unittest

import httpx

from app.ai.base import ProviderError
from tests.openai_target_fakes import make_catalog_target
from tests.provider_fakes import text_request


class ProviderTransportExceptionPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_error_text_cannot_expose_authorization_secret(self) -> None:
        secret = "PR58-TRANSPORT-TEXT-SECRET"

        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.LocalProtocolError(
                f"Illegal header value b'Bearer {secret}'",
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="privacy-model",
            credential=secret,
            target_id="chainnode:text:privacy:c2",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            headers={"Authorization": f"Bearer {secret}"},
            transport=httpx.MockTransport(fail),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        error = raised.exception
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, repr(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    async def test_transport_error_does_not_retain_authorization_request(self) -> None:
        secret = "PR58-TRANSPORT-SECRET"

        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection failed", request=request)

        provider = make_catalog_target(
            "chainnode",
            model="privacy-model",
            credential=secret,
            target_id="chainnode:text:privacy:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            headers={"Authorization": f"Bearer {secret}"},
            transport=httpx.MockTransport(fail),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        error = raised.exception
        self.assertIsNone(
            error.__cause__,
            "ProviderError must not retain the raw httpx exception as __cause__",
        )
        self.assertIsNone(
            error.__context__,
            "ProviderError must not retain the raw httpx exception as __context__",
        )


if __name__ == "__main__":
    unittest.main()
