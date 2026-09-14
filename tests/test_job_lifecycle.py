import unittest
from unittest.mock import AsyncMock

from app.main import POLLING_UPDATES, _shutdown_question_controls


class JobLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def test_polling_receives_callback_queries(self):
        self.assertIn('message', POLLING_UPDATES)
        self.assertIn('my_chat_member', POLLING_UPDATES)
        self.assertIn('callback_query', POLLING_UPDATES)

    async def test_controlled_shutdown_stops_manager_before_presenter(self):
        order = []
        manager = AsyncMock()
        presenter = AsyncMock()
        manager.shutdown.side_effect = lambda: order.append('manager')
        presenter.close.side_effect = lambda: order.append('presenter')

        await _shutdown_question_controls(manager, presenter)

        self.assertEqual(order, ['manager', 'presenter'])
        manager.shutdown.assert_awaited_once()
        presenter.close.assert_awaited_once()

    async def test_shutdown_helper_accepts_legacy_none(self):
        await _shutdown_question_controls(None, None)


if __name__ == '__main__':
    unittest.main()
