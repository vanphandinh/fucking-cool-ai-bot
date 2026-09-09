"""Offline regression tests for Telegram multimodal/vision support."""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace

import httpx

from app.ai.base import OpenAICompatProvider, ProviderError
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth
from app.ai.multimodal import build_user_content
from app.ai.router import AIProviderRouter
from app.bot.media import ImageTooLarge, TelegramMediaLoader
from app.config import Settings
from app.core.request import ImageAttachment, UserRequest


class VisionRequestTests(unittest.TestCase):
    def test_text_request_does_not_require_vision(self) -> None:
        self.assertFalse(UserRequest(text="hello").requires_vision)

    def test_image_request_requires_vision_and_tuple_is_immutable(self) -> None:
        image = ImageAttachment("image/jpeg", b"abc", "current")
        request = UserRequest(text="x", images=(image,))
        self.assertTrue(request.requires_vision)
        self.assertIsInstance(request.images, tuple)

    def test_domain_object_contains_no_base64_representation(self) -> None:
        image = ImageAttachment("image/png", b"binary", "reply")
        request = UserRequest(text="x", images=(image,))
        self.assertEqual(request.images[0].data, b"binary")
        self.assertNotIn("base64", repr(request))


class MultimodalPayloadTests(unittest.TestCase):
    def test_text_payload_stays_string(self) -> None:
        content = build_user_content(UserRequest(text="hello", quoted_text="old"))
        self.assertIsInstance(content, str)
        self.assertIn("old", content)
        self.assertIn("hello", content)

    def test_png_data_url_round_trip(self) -> None:
        raw = b"\x89PNG\r\n"
        content = build_user_content(
            UserRequest(text="read", images=(ImageAttachment("image/png", raw),))
        )
        self.assertIsInstance(content, list)
        image_url = content[-1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(image_url.split(",", 1)[1]), raw)

    def test_reply_image_precedes_question_and_current_image(self) -> None:
        request = UserRequest(
            text="compare",
            quoted_text="quoted",
            images=(
                ImageAttachment("image/jpeg", b"reply", "reply"),
                ImageAttachment("image/webp", b"current", "current"),
            ),
        )
        content = build_user_content(request)
        self.assertEqual(
            [part["type"] for part in content],
            ["text", "image_url", "text", "image_url"],
        )
        self.assertIn("quoted", content[0]["text"])
        self.assertIn("compare", content[2]["text"])


class CapabilityTests(unittest.TestCase):
    def test_text_slot_rejects_image(self) -> None:
        cap = ProviderCapabilities(route="text")
        self.assertTrue(cap.accepts(requires_vision=False, image_count=0))
        self.assertFalse(cap.accepts(requires_vision=True, image_count=1))

    def test_vision_slot_enforces_image_limit(self) -> None:
        cap = ProviderCapabilities(route="vision", supports_vision=True, max_images=1)
        self.assertTrue(cap.accepts(requires_vision=True, image_count=1))
        self.assertFalse(cap.accepts(requires_vision=True, image_count=2))
        self.assertFalse(cap.accepts(requires_vision=False, image_count=0))

    def test_router_separates_text_and_vision_slots(self) -> None:
        class P:
            def __init__(self, route: str, vision: bool, max_images: int) -> None:
                self.capabilities = ProviderCapabilities(route, vision, max_images)

        text = P("text", False, 0)
        vision = P("vision", True, 1)
        router = AIProviderRouter([text, vision])  # type: ignore[list-item]
        self.assertEqual(router.capable_providers(requires_vision=False), [text])
        self.assertEqual(router.capable_providers(requires_vision=True, image_count=1), [vision])
        self.assertEqual(router.capable_providers(requires_vision=True, image_count=2), [])


class HealthTests(unittest.TestCase):
    def test_429_enters_cooldown(self) -> None:
        health = ProviderHealth()
        health.record_error("quota", status_code=429, retry_after=10)
        self.assertFalse(health.available())
        self.assertGreater(health.cooldown_seconds(), 0)

    def test_403_disables_until_restart(self) -> None:
        health = ProviderHealth()
        health.record_error("denied", status_code=403, transient=False)
        self.assertTrue(health.disabled)
        self.assertFalse(health.available())

    def test_two_transient_failures_cool_down(self) -> None:
        health = ProviderHealth()
        health.record_error("network")
        self.assertTrue(health.available())
        health.record_error("network")
        self.assertFalse(health.available())


class VisionConfigTests(unittest.TestCase):
    def test_defaults_and_disable_switch(self) -> None:
        settings = Settings(_env_file=None, bai_api_key="x")
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.configured_vision_provider_names, ["bai"])
        disabled = Settings(_env_file=None, bai_api_key="x", vision_enabled=False)
        self.assertEqual(disabled.configured_vision_provider_names, [])

    def test_missing_bai_key_has_no_vision_provider(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.configured_vision_provider_names, [])


class ProviderErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_error_redacts_image_data_url(self) -> None:
        secret = "SECRET_IMAGE_BYTES"

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": f"invalid image data:image/png;base64,{secret}"}},
            )

        provider = OpenAICompatProvider("vision", "https://example.org/v1", "fake", "fake")
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/", transport=httpx.MockTransport(respond)
        )
        try:
            with self.assertRaises(ProviderError) as ctx:
                await provider.chat([{"role": "user", "content": "image"}])
            self.assertNotIn(secret, str(ctx.exception))
        finally:
            await provider.aclose()


class TelegramMediaSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_file_size_is_bounded_during_download(self) -> None:
        completed_writes: list[int] = []

        class FakeBot:
            async def download(self, _file_id: str, destination) -> None:
                destination.write(b"1234")
                completed_writes.append(1)
                destination.write(b"56")
                completed_writes.append(2)

        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo", file_size=None)],
            document=None,
            bot=FakeBot(),
        )
        loader = TelegramMediaLoader(
            Settings(_env_file=None, max_image_bytes=5, max_total_image_bytes=5)
        )

        with self.assertRaises(ImageTooLarge):
            await loader._from_message(message, "current")
        self.assertEqual(completed_writes, [1])


if __name__ == "__main__":
    unittest.main()
