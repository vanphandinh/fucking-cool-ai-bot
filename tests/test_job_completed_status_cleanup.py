import unittest

from app.bot.job_status import JobStatusPresenter
from app.config import Settings
from app.core.job_manager import JobSnapshot


class _SnapshotManager:
    def __init__(self, snapshot):
        self.current = snapshot

    def snapshot(self, job_id):
        if job_id != self.current.job_id:
            return None
        return self.current


class _RecordingBot:
    def __init__(self):
        self.deleted = []
        self.edited = []

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)


class CompletedStatusCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_job_deletes_status_instead_of_rendering_completion(self):
        snapshot = JobSnapshot(
            job_id="job-done",
            owner_id=10,
            chat_id=-100,
            topic_id=7,
            request_message_id=1,
            status_message_id=101,
            state="COMPLETED",
            generation=0,
            renewals=0,
            active_operations=0,
            created_at=0.0,
            active_duration=1.0,
            result_ready=True,
            error=None,
        )
        manager = _SnapshotManager(snapshot)
        bot = _RecordingBot()
        presenter = JobStatusPresenter(bot, Settings(_env_file=None), manager)

        await presenter.refresh(snapshot.job_id, force=True)

        self.assertEqual(
            bot.deleted,
            [{"chat_id": snapshot.chat_id, "message_id": snapshot.status_message_id}],
        )
        self.assertEqual(bot.edited, [])


if __name__ == "__main__":
    unittest.main()
