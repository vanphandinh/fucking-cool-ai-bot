"""Offline regression tests for Telegram multimodal/vision support."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import httpx

from app.ai.base import ProviderError
from app.ai.contracts import ChatMessage, ChatRequest, ImagePart, TextPart
from app.ai.drivers.openai_chat import OpenAIChatDriver
from app.ai.recovery import RecoveryPolicy
from app.ai.target import ProviderTargetIdentity, TargetSpec
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth
from app.ai.multimodal import build_user_parts
from app.ai.router import AIProviderRouter, build_provider_router
from app.bot.media import ImageTooLarge, TelegramMediaLoader
from app.config import Settings
from app.core.request import ImageAttachment, UserRequest
from tests.provider_fakes import ScriptedProvider


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
    def test_text_payload_uses_canonical_text_part(self) -> None:
        content = build_user_parts(UserRequest(text="hello", quoted_text="old"))
        self.assertEqual(len(content), 1)
        self.assertIsInstance(content[0], TextPart)
        self.assertIn("old", content[0].text)
        self.assertIn("hello", content[0].text)

    def test_png_bytes_remain_raw_until_driver_serialization(self) -> None:
        raw = b"\x89PNG\r\n"
        content = build_user_parts(
            UserRequest(text="read", images=(ImageAttachment("image/png", raw),))
        )
        self.assertEqual([type(part) for part in content], [TextPart, ImagePart])
        self.assertEqual(content[-1].mime_type, "image/png")
        self.assertEqual(content[-1].data, raw)
        self.assertNotIn("base64", repr(content))

    def test_reply_image_precedes_question_and_current_image(self) -> None:
        request = UserRequest(
            text="compare",
            quoted_text="quoted",
            images=(
                ImageAttachment("image/jpeg", b"reply", "reply"),
                ImageAttachment("image/webp", b"current", "current"),
            ),
        )
        content = build_user_parts(request)
        self.assertEqual(
            [type(part) for part in content],
            [TextPart, ImagePart, TextPart, ImagePart],
        )
        self.assertIn("quoted", content[0].text)
        self.assertEqual(content[1].data, b"reply")
        self.assertIn("compare", content[2].text)
        self.assertEqual(content[3].data, b"current")


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
        text = ScriptedProvider("test", [])
        vision = ScriptedProvider("test", [], route="vision", max_images=1)
        router = AIProviderRouter([text, vision])
        self.assertEqual(router.capable_providers(requires_vision=False), [text])
        self.assertEqual(
            router.capable_providers(requires_vision=True, image_count=1),
            [vision],
        )
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
    def _xkiro_settings(self, **overrides) -> Settings:
        values = {
            "ai_providers": {
                "xkiro": {
                    "api_keys": "test-key",
                    "text_models": "test-text-model",
                    "vision_models": "test-vision-model",
                }
            }
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_defaults_and_disable_switch(self) -> None:
        router = build_provider_router(self._xkiro_settings())
        try:
            self.assertEqual(router.configured_provider_names(True), ("xkiro",))
            self.assertEqual(router.max_supported_images(), 1)
        finally:
            asyncio.run(_close_router(router))

        disabled = build_provider_router(self._xkiro_settings(vision_enabled=False))
        try:
            self.assertEqual(disabled.configured_provider_names(True), ())
            self.assertEqual(disabled.max_supported_images(), 0)
        finally:
            asyncio.run(_close_router(disabled))

    def test_missing_xkiro_key_has_no_vision_provider(self) -> None:
        router = build_provider_router(Settings(_env_file=None))
        self.assertEqual(router.configured_provider_names(True), ())
        self.assertEqual(router.max_supported_images(), 0)

    def test_current_xkiro_vision_route_rejects_two_images(self) -> None:
        router = build_provider_router(self._xkiro_settings())
        try:
            self.assertEqual(
                [
                    p.name
                    for p in router.capable_providers(
                        requires_vision=True,
                        image_count=1,
                    )
                ],
                ["xkiro"],
            )
            self.assertEqual(
                router.capable_providers(requires_vision=True, image_count=2),
                [],
            )
        finally:
            asyncio.run(_close_router(router))


class ProviderErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_error_redacts_image_data_url(self) -> None:
        secret = "SECRET_IMAGE_BYTES"

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": f"invalid image data:image/png;base64,{secret}"}},
            )

        spec = TargetSpec(
            identity=ProviderTargetIdentity(
                family="vision",
                route="vision",
                model="test-model",
                credential_id="cred-1",
                target_id="vision:vision:m1:c1",
            ),
            driver="openai-chat",
            capabilities=ProviderCapabilities(
                route="vision", supports_vision=True, max_images=1
            ),
            base_url="https://example.org/v1",
            request_timeout_sec=60.0,
            recovery_policy=RecoveryPolicy(),
            driver_options={"explicit_stream": False},
        )
        provider = OpenAIChatDriver().build_target(spec, credential="test-key")
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/", transport=httpx.MockTransport(respond)
        )
        try:
            with self.assertRaises(ProviderError) as ctx:
                await provider.chat(
                    ChatRequest(
                        messages=(ChatMessage("user", (TextPart("image"),)),)
                    )
                )
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


class ForwardedVideoTests(unittest.IsolatedAsyncioTestCase):
    """Video (kể cả forward) chỉ xử lý khi đã tag — loader cover 3 nguồn media."""

    def _video_message(self, bot, **overrides):
        base = {
            "photo": None,
            "document": None,
            "video": SimpleNamespace(
                file_id="vid1",
                file_size=100,
                duration=10,
                thumbnail=SimpleNamespace(file_id="thumb1", file_size=10),
            ),
            "animation": None,
            "video_note": None,
            "reply_to_message": None,
            "external_reply": None,
            "forward_origin": SimpleNamespace(type="channel"),
            "bot": bot,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _bot(self, payloads):
        class FakeBot:
            async def download(self, file_id: str, destination) -> None:
                destination.write(payloads.get(file_id, b"x"))

        return FakeBot()

    async def test_forwarded_video_becomes_one_jpeg_frame(self) -> None:
        from unittest.mock import patch

        bot = self._bot({"vid1": b"video-bytes", "thumb1": b"thumb"})
        message = self._video_message(bot)
        loader = TelegramMediaLoader(Settings(_env_file=None))
        with patch(
            "app.bot.media.extract_middle_frame", return_value=b"frame-jpeg"
        ):
            images = await loader.load(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime_type, "image/jpeg")
        self.assertEqual(images[0].data, b"frame-jpeg")
        self.assertEqual(images[0].source, "current")

    async def test_reply_to_forwarded_video_reads_reply_source(self) -> None:
        from unittest.mock import patch

        bot = self._bot({"vid1": b"video-bytes"})
        reply = self._video_message(bot)
        message = SimpleNamespace(
            photo=None,
            document=None,
            video=None,
            animation=None,
            video_note=None,
            text="@FuckingCoolAIbot video này nói gì?",
            reply_to_message=reply,
            external_reply=None,
            bot=bot,
        )
        loader = TelegramMediaLoader(Settings(_env_file=None))
        with patch(
            "app.bot.media.extract_middle_frame", return_value=b"frame-jpeg"
        ):
            images = await loader.load(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].source, "reply")

    async def test_external_reply_video_is_read(self) -> None:
        from unittest.mock import patch

        bot = self._bot({"vid1": b"video-bytes"})
        external = self._video_message(bot)
        external.reply_to_message = None
        message = SimpleNamespace(
            photo=None,
            document=None,
            video=None,
            animation=None,
            video_note=None,
            text="@FuckingCoolAIbot xem giúp",
            reply_to_message=None,
            external_reply=external,
            bot=bot,
        )
        loader = TelegramMediaLoader(Settings(_env_file=None))
        with patch(
            "app.bot.media.extract_middle_frame", return_value=b"frame-jpeg"
        ):
            images = await loader.load(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].source, "reply")

    async def test_oversize_video_rejected_before_download(self) -> None:
        downloaded: list[str] = []

        class FakeBot:
            async def download(self, file_id: str, destination) -> None:
                downloaded.append(file_id)
                destination.write(b"x")

        message = self._video_message(FakeBot())
        loader = TelegramMediaLoader(Settings(_env_file=None, max_video_bytes=10))
        with self.assertRaises(ImageTooLarge) as ctx:
            await loader.load(message)
        self.assertIn("Video", str(ctx.exception))
        self.assertEqual(downloaded, [])

    async def test_overlong_video_rejected(self) -> None:
        bot = self._bot({})
        message = self._video_message(bot)
        loader = TelegramMediaLoader(
            Settings(_env_file=None, max_video_duration_sec=5)
        )
        with self.assertRaises(ImageTooLarge) as ctx:
            await loader.load(message)
        self.assertIn("dài", str(ctx.exception))

    async def test_ffmpeg_failure_falls_back_to_thumbnail(self) -> None:
        from unittest.mock import patch

        from app.bot.video_frames import VideoFrameError

        bot = self._bot({"thumb1": b"thumb-bytes"})
        message = self._video_message(bot)
        loader = TelegramMediaLoader(Settings(_env_file=None))
        with patch(
            "app.bot.media.extract_middle_frame",
            side_effect=VideoFrameError("no ffmpeg"),
        ):
            images = await loader.load(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data, b"thumb-bytes")

    async def test_video_disabled_returns_no_images(self) -> None:
        bot = self._bot({})
        message = self._video_message(bot)
        loader = TelegramMediaLoader(
            Settings(_env_file=None, vision_video_enabled=False)
        )
        self.assertEqual(await loader.load(message), ())

    async def test_forward_without_tag_does_not_trigger(self) -> None:
        from app.bot.filters import TriggeredMessage

        settings = Settings(_env_file=None, bot_username="@FuckingCoolAIbot")
        filt = TriggeredMessage(settings)
        message = SimpleNamespace(
            text=None, caption=None, reply_to_message=None
        )
        self.assertFalse(await filt(message))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
