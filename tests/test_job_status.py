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


class JobStatusPresenterTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_owns_message_before_controls_become_clickable(self):
        events = []
        snapshot = JobSnapshot(
            job_id="abcd1234",
            owner_id=10,
            chat_id=-100,
            topic_id=7,
            request_message_id=1,
            status_message_id=101,
            state="RUNNING",
            generation=0,
            renewals=0,
            active_operations=1,
            created_at=0.0,
            active_duration=0.0,
            result_ready=False,
            error=None,
        )
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


if __name__ == "__main__":
    unittest.main()
