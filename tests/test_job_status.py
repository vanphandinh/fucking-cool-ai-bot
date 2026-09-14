import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from app.bot.job_status import JobStatusPresenter
from app.config import Settings
from app.core.job_manager import JobSnapshot


class _ReplacementManager:
    def __init__(self, snapshot, events):
        self.current = snapshot
        self.events = events

    def snapshot(self, job_id):
        if job_id != self.current.job_id:
            return None
        return self.current

    async def set_status_message_id(self, job_id, expected_message_id, new_message_id):
        self.events.append(("set", expected_message_id, new_message_id))
        if job_id != self.current.job_id:
            return False
        if expected_message_id != self.current.status_message_id:
            return False
        self.current = replace(self.current, status_message_id=new_message_id)
        return True


class _ReplacementBot:
    def __init__(self, events):
        self.events = events

    async def edit_message_text(self, **kwargs):
        raise TelegramBadRequest(
            method=SimpleNamespace(),
            message="Bad Request: message to edit not found",
        )

    async def send_message(self, **kwargs):
        self.events.append(("send", kwargs.get("reply_markup")))
        return SimpleNamespace(message_id=202)

    async def edit_message_reply_markup(self, **kwargs):
        self.events.append(
            ("markup", kwargs["message_id"], kwargs.get("reply_markup"))
        )


class _BlockingReplacementBot(_ReplacementBot):
    def __init__(self, events, send_entered, release_send):
        super().__init__(events)
        self.send_entered = send_entered
        self.release_send = release_send
        self.send_count = 0
        self.send_cancelled = 0

    async def send_message(self, **kwargs):
        self.send_count += 1
        self.events.append(("send", kwargs.get("reply_markup")))
        self.send_entered.set()
        try:
            await self.release_send.wait()
        except asyncio.CancelledError:
            self.send_cancelled += 1
            raise
        return SimpleNamespace(message_id=202)


class _RetryReplacementBot(_ReplacementBot):
    def __init__(self, events):
        super().__init__(events)
        self.send_count = 0

    async def send_message(self, **kwargs):
        self.send_count += 1
        self.events.append(("send", kwargs.get("reply_markup")))
        if self.send_count == 1:
            raise RuntimeError("temporary telegram failure")
        return SimpleNamespace(message_id=202)


class JobStatusPresenterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _snapshot(*, state="RUNNING"):
        return JobSnapshot(
            job_id="abcd1234",
            owner_id=10,
            chat_id=-100,
            topic_id=7,
            request_message_id=1,
            status_message_id=101,
            state=state,
            generation=0,
            renewals=0,
            active_operations=1,
            created_at=0.0,
            active_duration=0.0,
            result_ready=False,
            error=None,
        )

    async def test_replacement_owns_message_before_controls_become_clickable(self):
        events = []
        snapshot = self._snapshot()
        manager = _ReplacementManager(snapshot, events)
        bot = _ReplacementBot(events)
        settings = Settings(_env_file=None)
        presenter = JobStatusPresenter(bot, settings, manager)

        await presenter.refresh(snapshot.job_id, force=True)

        self.assertEqual(events[0], ("send", None))
        self.assertEqual(events[1], ("set", 101, 202))
        self.assertEqual(events[2][0:2], ("markup", 202))
        self.assertIsNotNone(events[2][2])
        self.assertEqual(manager.snapshot(snapshot.job_id).status_message_id, 202)

    async def test_force_refresh_does_not_cancel_active_replacement(self):
        events = []
        send_entered = asyncio.Event()
        release_send = asyncio.Event()
        snapshot = self._snapshot(state="AWAITING_CONSENT")
        manager = _ReplacementManager(snapshot, events)
        bot = _BlockingReplacementBot(events, send_entered, release_send)
        settings = Settings(_env_file=None)
        presenter = JobStatusPresenter(bot, settings, manager)

        presenter.enqueue(snapshot.job_id, force=True)
        await asyncio.wait_for(send_entered.wait(), timeout=1)

        presenter.enqueue(snapshot.job_id, force=True)
        release_send.set()
        while presenter._tasks:
            await asyncio.gather(*tuple(presenter._tasks), return_exceptions=True)

        self.assertEqual(bot.send_count, 1)
        self.assertEqual(bot.send_cancelled, 0)
        self.assertEqual(manager.snapshot(snapshot.job_id).status_message_id, 202)
        markup_events = [event for event in events if event[0] == "markup"]
        self.assertEqual(len(markup_events), 1)
        self.assertIsNotNone(markup_events[0][2])

    async def test_failed_replacement_send_is_retryable(self):
        events = []
        snapshot = self._snapshot(state="AWAITING_CONSENT")
        manager = _ReplacementManager(snapshot, events)
        bot = _RetryReplacementBot(events)
        settings = Settings(_env_file=None)
        presenter = JobStatusPresenter(bot, settings, manager)

        await presenter.refresh(snapshot.job_id, force=True)
        await presenter.refresh(snapshot.job_id, force=True)

        self.assertEqual(bot.send_count, 2)
        self.assertEqual(manager.snapshot(snapshot.job_id).status_message_id, 202)
        markup_events = [event for event in events if event[0] == "markup"]
        self.assertEqual(len(markup_events), 1)
        self.assertIsNotNone(markup_events[0][2])


if __name__ == "__main__":
    unittest.main()
