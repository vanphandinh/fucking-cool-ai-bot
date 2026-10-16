"""Production cleanup must survive failures without hiding the original error."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import main
from app.config import Settings
from app.search import runtime


class ShutdownFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_failure_still_closes_presenter(self):
        manager = SimpleNamespace(shutdown=AsyncMock(side_effect=RuntimeError("manager")))
        presenter = SimpleNamespace(close=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, "manager"):
            await main._shutdown_question_controls(manager, presenter)
        presenter.close.assert_awaited_once()

    async def test_control_shutdown_failure_still_closes_network_resources(self):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="testbot", first_name="Test")),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        provider = SimpleNamespace(aclose=AsyncMock())
        router = SimpleNamespace(
            providers=[provider],
            configured_provider_names=lambda vision: [] if vision else ["chainnode"],
        )
        with (
            patch.object(main, "Bot", return_value=bot),
            patch.object(main, "build_provider_router", return_value=router),
            patch.object(main.Dispatcher, "start_polling", new=AsyncMock()),
            patch.object(main, "_shutdown_question_controls", new=AsyncMock(
                side_effect=RuntimeError("controls"))),
            patch.object(main, "close_crawl4ai_client", new=AsyncMock()) as crawler,
            patch.object(main, "close_search_runtimes", new=AsyncMock()) as search,
        ):
            with self.assertRaisesRegex(RuntimeError, "controls"):
                await main._amain(Settings(_env_file=None, bot_token="123:test"))
        bot.session.close.assert_awaited_once()
        crawler.assert_awaited_once()
        search.assert_awaited_once()
        provider.aclose.assert_awaited_once()

    async def test_search_close_failure_does_not_abandon_other_clients(self):
        first = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("first")))
        second = SimpleNamespace(aclose=AsyncMock())
        with patch.dict(runtime._runtimes, {1: first, 2: second}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "first"):
                await runtime.close_search_runtimes()
            self.assertEqual(runtime._runtimes, {})
        first.aclose.assert_awaited_once()
        second.aclose.assert_awaited_once()

    async def test_multiple_search_errors_remain_observable(self):
        first = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("first")))
        second = SimpleNamespace(aclose=AsyncMock(side_effect=ValueError("second")))
        third = SimpleNamespace(aclose=AsyncMock())
        with patch.dict(runtime._runtimes, {1: first, 2: second, 3: third}, clear=True):
            with self.assertRaises(ExceptionGroup) as raised:
                await runtime.close_search_runtimes()
        self.assertEqual([type(exc) for exc in raised.exception.exceptions],
                         [RuntimeError, ValueError])
        third.aclose.assert_awaited_once()

    async def test_presenter_closes_when_manager_is_cancelled(self):
        import asyncio

        manager = SimpleNamespace(shutdown=AsyncMock(side_effect=asyncio.CancelledError))
        presenter = SimpleNamespace(close=AsyncMock())
        with self.assertRaises(asyncio.CancelledError):
            await main._shutdown_question_controls(manager, presenter)
        presenter.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
