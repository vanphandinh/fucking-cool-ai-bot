"""Regression tests for issues found during the PR #38 audit."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.ai.base import AllProvidersFailed
from app.ai.router import build_provider_router
from app.bot.handlers import _ChatLocks, _handle_question
from app.config import Settings
from app.core.context import ChatMemory
from app.core.orchestrator import Answer, Orchestrator
from app.core.rate_limiter import RateLimiter
from app.core.request import ImageAttachment
from app.core.stats import Stats


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


class ProviderConstructionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unused_vision_slot_is_not_validated_or_built(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="secret",
            text_provider_order="bai",
            vision_provider_order="",
            vision_enabled=True,
            bai_vision_model="hy3",
        )

        try:
            router = build_provider_router(settings)
        except ValueError as exc:
            self.fail(f"unused vision slot should not be validated: {exc}")

        try:
            self.assertEqual(
                [provider.capabilities.route for provider in router.providers],
                ["text"],
            )
        finally:
            await _close_router(router)

    async def test_unused_text_slot_is_not_validated_or_built(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="secret",
            text_provider_order="",
            vision_provider_order="bai",
            vision_enabled=True,
            bai_text_model="not-a-supported-model",
            bai_vision_model="qwen3.8-flash",
        )

        try:
            router = build_provider_router(settings)
        except ValueError as exc:
            self.fail(f"unused text slot should not be validated: {exc}")

        try:
            self.assertEqual(
                [provider.capabilities.route for provider in router.providers],
                ["vision"],
            )
        finally:
            await _close_router(router)


class VisionAdmissionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_rejects_images_above_effective_provider_limit(self) -> None:
        images = (
            ImageAttachment("image/jpeg", b"one", "current"),
            ImageAttachment("image/jpeg", b"two", "reply"),
        )
        media_loader = SimpleNamespace(load=AsyncMock(return_value=images))
        provider_router = SimpleNamespace(max_supported_images=lambda: 1)
        orchestrator = SimpleNamespace(
            router=provider_router,
            ask=AsyncMock(return_value=Answer("should not reach AI", "bai")),
        )
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=-100200),
            message_thread_id=None,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(),
            ),
            reply=AsyncMock(),
        )

        await _handle_question(
            message,
            "compare",
            None,
            Settings(_env_file=None, max_images_per_request=3),
            orchestrator,
            ChatMemory(),
            RateLimiter(),
            Stats(),
            _ChatLocks(),
            media_loader=media_loader,
        )

        orchestrator.ask.assert_not_awaited()
        replies = [
            str(call.args[0])
            for call in message.reply.await_args_list
            if call.args
        ]
        self.assertTrue(any("tối đa 1 ảnh" in reply for reply in replies), replies)


class RuntimeErrorRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_does_not_reclassify_programming_error_as_provider_failure(
        self,
    ) -> None:
        router = SimpleNamespace(
            complete=AsyncMock(side_effect=RuntimeError("programming bug"))
        )
        orchestrator = Orchestrator(Settings(_env_file=None), router)

        try:
            await orchestrator.ask(question="hello")
        except AllProvidersFailed as exc:
            self.fail(f"runtime error was hidden as provider failure: {exc}")
        except RuntimeError as exc:
            self.assertEqual(str(exc), "programming bug")
        else:
            self.fail("expected RuntimeError")


class ShutdownRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_session_close_failure_still_closes_other_clients(self) -> None:
        from app import main

        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(username="testbot", first_name="Test")
            ),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(
                close=AsyncMock(side_effect=RuntimeError("telegram close failed"))
            ),
        )
        provider = SimpleNamespace(name="bai", aclose=AsyncMock())
        provider_router = SimpleNamespace(
            providers=[provider],
            configured_provider_names=lambda requires_vision: (
                () if requires_vision else ("bai",)
            ),
        )
        close_crawl = AsyncMock()
        close_search = AsyncMock()

        with (
            patch.object(main, "Bot", return_value=bot),
            patch.object(main, "build_provider_router", return_value=provider_router),
            patch.object(main.Dispatcher, "start_polling", new=AsyncMock()),
            patch.object(main, "close_crawl4ai_client", close_crawl),
            patch.object(main, "close_search_runtimes", close_search),
        ):
            with self.assertRaisesRegex(RuntimeError, "telegram close failed"):
                await main._amain(
                    Settings(
                        _env_file=None,
                        bot_token="123:test",
                        bai_api_key="secret",
                        vision_enabled=False,
                    )
                )

        close_crawl.assert_awaited_once()
        close_search.assert_awaited_once()
        provider.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
