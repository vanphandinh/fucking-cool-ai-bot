import time
import unittest
from types import SimpleNamespace

from app.core.job_manager import JobSnapshot
from app.bot.job_callbacks import handle_job_callback, make_callback_data, parse_callback_data
from app.bot.job_status import render_job_status


class FakeQuery:
    def __init__(self, *, data, actor=10, chat=-100, message_id=101):
        self.data = data
        self.from_user = SimpleNamespace(id=actor)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat),
            message_id=message_id,
        )
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class FakeManager:
    def __init__(self):
        self.calls = []
        self.query = None

    async def renew(self, job_id, generation, actor_id, chat_id, status_message_id):
        self.assert_acked()
        self.calls.append(('renew', job_id, generation, actor_id, chat_id, status_message_id))
        return 'renewed'

    async def stop(self, job_id, actor_id, chat_id, status_message_id):
        self.assert_acked()
        self.calls.append(('stop', job_id, actor_id, chat_id, status_message_id))
        return 'stopped'

    def assert_acked(self):
        if self.query is not None:
            assert self.query.answered


class JobTelegramTests(unittest.IsolatedAsyncioTestCase):
    def test_callback_round_trip_and_limit(self):
        value = make_callback_data('0123456789abcdef', 123, 'c')
        self.assertLessEqual(len(value.encode('utf-8')), 64)
        self.assertEqual(parse_callback_data(value), ('0123456789abcdef', 123, 'c'))
        self.assertIsNone(parse_callback_data('other:abc'))

    async def test_callback_is_acknowledged_before_mutation(self):
        manager = FakeManager()
        query = FakeQuery(data=make_callback_data('abc', 7, 'c'))
        manager.query = query
        result = await handle_job_callback(query, manager)
        self.assertTrue(query.answered)
        self.assertEqual(result, 'renewed')
        self.assertEqual(manager.calls[0][0], 'renew')

    async def test_stop_ignores_stale_generation_but_continue_passes_generation(self):
        manager = FakeManager()
        stop = FakeQuery(data=make_callback_data('abc', 1, 's'))
        manager.query = stop
        self.assertEqual(await handle_job_callback(stop, manager), 'stopped')
        self.assertEqual(manager.calls[-1][0], 'stop')

        cont = FakeQuery(data=make_callback_data('abc', 8, 'c'))
        manager.query = cont
        self.assertEqual(await handle_job_callback(cont, manager), 'renewed')
        self.assertEqual(manager.calls[-1][2], 8)

    async def test_inaccessible_message_is_safe_after_ack(self):
        manager = FakeManager()
        query = FakeQuery(data=make_callback_data('abc', 1, 'c'))
        query.message = None
        manager.query = query
        self.assertEqual(await handle_job_callback(query, manager), 'invalid')
        self.assertTrue(query.answered)
        self.assertEqual(manager.calls, [])

    def test_awaiting_consent_renders_continue_stop_and_no_fake_percent(self):
        snapshot = JobSnapshot(
            job_id='0123456789abcdef',
            owner_id=10,
            chat_id=-100,
            topic_id=7,
            request_message_id=1,
            status_message_id=101,
            state='AWAITING_CONSENT',
            generation=4,
            renewals=3,
            active_operations=1,
            created_at=time.monotonic() - 192,
            active_duration=181,
            result_ready=False,
            error=None,
        )
        text, markup = render_job_status(snapshot, renewal_sec=180)
        self.assertIn('lâu hơn dự kiến', text)
        self.assertIn('tiếp tục thêm 3 phút', text.lower())
        self.assertIn('Đang hoàn tất bước hiện tại', text)
        self.assertNotIn('%', text)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(make_callback_data(snapshot.job_id, 4, 'c'), callbacks)
        self.assertIn(make_callback_data(snapshot.job_id, 4, 's'), callbacks)

    def test_terminal_status_has_no_buttons(self):
        snapshot = JobSnapshot(
            job_id='job', owner_id=1, chat_id=2, topic_id=None,
            request_message_id=3, status_message_id=4, state='COMPLETED',
            generation=1, renewals=0, active_operations=0,
            created_at=time.monotonic() - 5, active_duration=2,
            result_ready=True, error=None,
        )
        text, markup = render_job_status(snapshot, renewal_sec=180)
        self.assertIn('Hoàn tất', text)
        self.assertIsNone(markup)


if __name__ == '__main__':
    unittest.main()
