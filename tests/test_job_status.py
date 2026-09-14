import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

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

    async def set_status_message_id(
        self,
        job_id,
        expected_message_id,
        new_message_id,
    ):
        self.events.append(("set", expected_message_id, new_message_id))
        if job_id != self.current.job_id:
            return False
        if expected_message_id != self.current.status_message_id:
            return False
        self.current = replace(self.current, status_message_id=new_message_id)
        return True


class _SnapshotManager:
    def __init__(self, snapshots):
        self.snapshots = {snapshot.job_id: snapshot for snapshot in snapshots}

    def snapshot(self, job_id):
        return self.snapshots.get(job_id)


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


class _SuccessBot:
    async def edit_message_text(self, **kwargs):
        return None


class _RecordingBot:
    def __init__(self):
        self.calls = []
        self.called = asyncio.Event()

    async def edit_message_text(self, **kwargs):
        self.calls.append(kwargs)
        self.called.set()


class _AlwaysFailBot:
    def __init__(self):
        self.attempts = 0

    async def edit_message_text(self, **kwargs):
        self.attempts += 1
        raise RuntimeError("telegram unavailable")


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


class _MarkupRetryBot(_ReplacementBot):
    def __init__(self, events):
        super().__init__(events)
        self.send_count = 0
        self.markup_attempts = 0

    async def edit_message_text(self, **kwargs):
        if kwargs["message_id"] == 101:
            raise TelegramBadRequest(
                method=SimpleNamespace(),
                message="Bad Request: message to edit not found",
            )
        self.events.append(
            ("edit", kwargs["message_id"], kwargs.get("reply_markup"))
        )

    async def send_message(self, **kwargs):
        self.send_count += 1
        self.events.append(("send", kwargs.get("reply_markup")))
        return SimpleNamespace(message_id=202)

    async def edit_message_reply_markup(self, **kwargs):
        self.markup_attempts += 1
        self.events.append(
            ("markup", kwargs["message_id"], kwargs.get("reply_markup"))
        )
        if self.markup_attempts == 1:
            raise RuntimeError("temporary markup failure")


class JobStatusPresenterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _snapshot(*, state="RUNNING", job_id="abcd1234", status_message_id=101):
        return JobSnapshot(
            job_id=job_id,
            owner_id=10,
            chat_id=-100,
            topic_id=7,
            request_message_id=1,
            status_message_id=status_message_id,
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
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

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
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

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

    async def test_forced_refresh_wakes_progress_throttle(self):
        events = []
        snapshot = self._snapshot(state="RUNNING")
        manager = _ReplacementManager(snapshot, events)
        bot = _RecordingBot()
        settings = Settings(
            _env_file=None,
            question_progress_interval_sec=.5,
        )
        presenter = JobStatusPresenter(bot, settings, manager)

        try:
            presenter.enqueue(snapshot.job_id, force=True)
            await asyncio.wait_for(bot.called.wait(), timeout=.2)
            bot.called.clear()

            presenter._states[snapshot.job_id].stage_label = "slow stage"
            presenter.enqueue(snapshot.job_id)
            await asyncio.sleep(.02)
            self.assertFalse(presenter._scheduled[snapshot.job_id].done())

            manager.current = replace(
                manager.current,
                state="AWAITING_CONSENT",
                active_operations=0,
            )
            presenter.enqueue(snapshot.job_id, force=True)
            await asyncio.wait_for(bot.called.wait(), timeout=.15)

            self.assertEqual(len(bot.calls), 2)
            self.assertIn("Quá trình lâu hơn dự kiến", bot.calls[-1]["text"])
        finally:
            await presenter.close()

    async def test_failed_replacement_send_recovers_automatically(self):
        events = []
        snapshot = self._snapshot(state="AWAITING_CONSENT")
        manager = _ReplacementManager(snapshot, events)
        bot = _RetryReplacementBot(events)
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

        with (
            patch("app.bot.job_status._STATUS_RETRY_BASE_SEC", .01),
            patch("app.bot.job_status._STATUS_RETRY_MAX_SEC", .02),
        ):
            presenter.enqueue(snapshot.job_id, force=True)
            while presenter._tasks:
                await asyncio.gather(
                    *tuple(presenter._tasks),
                    return_exceptions=True,
                )

        self.assertEqual(bot.send_count, 2)
        self.assertEqual(manager.snapshot(snapshot.job_id).status_message_id, 202)
        markup_events = [event for event in events if event[0] == "markup"]
        self.assertEqual(len(markup_events), 1)
        self.assertIsNotNone(markup_events[0][2])

    async def test_replacement_markup_recovers_automatically_on_owned_message(self):
        events = []
        snapshot = self._snapshot(state="AWAITING_CONSENT")
        manager = _ReplacementManager(snapshot, events)
        bot = _MarkupRetryBot(events)
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

        with (
            patch("app.bot.job_status._STATUS_RETRY_BASE_SEC", .01),
            patch("app.bot.job_status._STATUS_RETRY_MAX_SEC", .02),
        ):
            presenter.enqueue(snapshot.job_id, force=True)
            while presenter._tasks:
                await asyncio.gather(
                    *tuple(presenter._tasks),
                    return_exceptions=True,
                )

        self.assertEqual(bot.send_count, 1)
        self.assertEqual(manager.snapshot(snapshot.job_id).status_message_id, 202)
        edit_events = [event for event in events if event[0] == "edit"]
        self.assertEqual(len(edit_events), 1)
        self.assertEqual(edit_events[0][1], 202)
        self.assertIsNotNone(edit_events[0][2])

    async def test_permanent_failure_does_not_tight_loop(self):
        snapshot = self._snapshot(state="AWAITING_CONSENT")
        manager = _SnapshotManager([snapshot])
        bot = _AlwaysFailBot()
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

        try:
            with (
                patch("app.bot.job_status._STATUS_RETRY_BASE_SEC", .05),
                patch("app.bot.job_status._STATUS_RETRY_MAX_SEC", .05),
            ):
                presenter.enqueue(snapshot.job_id, force=True)
                await asyncio.sleep(.02)
                self.assertEqual(bot.attempts, 1)
        finally:
            await presenter.close()

    async def test_terminal_refresh_releases_presenter_bookkeeping(self):
        snapshot = self._snapshot(state="COMPLETED")
        manager = _SnapshotManager([snapshot])
        presenter = JobStatusPresenter(
            _SuccessBot(),
            Settings(_env_file=None),
            manager,
        )

        presenter.enqueue(snapshot.job_id, force=True)
        while presenter._tasks:
            await asyncio.gather(*tuple(presenter._tasks), return_exceptions=True)

        self.assertNotIn(snapshot.job_id, presenter._states)
        self.assertNotIn(snapshot.job_id, presenter._locks)
        self.assertNotIn(snapshot.job_id, presenter._scheduled)

    async def test_many_completed_jobs_do_not_accumulate_presenter_state(self):
        snapshots = [
            self._snapshot(
                state="COMPLETED",
                job_id=f"job-{index}",
                status_message_id=1000 + index,
            )
            for index in range(20)
        ]
        manager = _SnapshotManager(snapshots)
        presenter = JobStatusPresenter(
            _SuccessBot(),
            Settings(_env_file=None),
            manager,
        )

        for snapshot in snapshots:
            presenter.enqueue(snapshot.job_id, force=True)
        while presenter._tasks:
            await asyncio.gather(*tuple(presenter._tasks), return_exceptions=True)

        self.assertEqual(presenter._states, {})
        self.assertEqual(presenter._locks, {})
        self.assertEqual(presenter._scheduled, {})

    async def test_close_clears_presenter_bookkeeping(self):
        snapshot = self._snapshot()
        manager = _SnapshotManager([snapshot])
        presenter = JobStatusPresenter(
            _SuccessBot(),
            Settings(_env_file=None),
            manager,
        )

        await presenter.refresh(snapshot.job_id, force=True)
        self.assertIn(snapshot.job_id, presenter._states)
        self.assertIn(snapshot.job_id, presenter._locks)

        await presenter.close()

        self.assertEqual(presenter._tasks, set())
        self.assertEqual(presenter._scheduled, {})
        self.assertEqual(presenter._states, {})
        self.assertEqual(presenter._locks, {})


if __name__ == "__main__":
    unittest.main()
