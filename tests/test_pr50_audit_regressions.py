import unittest
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from app.bot.job_status import JobStatusPresenter


class CompletedStatusDeletionRegressionTests(unittest.TestCase):
    def test_delete_missing_error_is_classified_as_missing_message(self):
        exc = TelegramBadRequest(
            method=SimpleNamespace(),
            message="Bad Request: message to delete not found",
        )

        self.assertTrue(JobStatusPresenter._is_missing_message(exc))


if __name__ == "__main__":
    unittest.main()
