"""Liveness: a heartbeat that only moves while the bot can still reach Telegram.

The point of the health check is the negative case, so these tests pin it: a
failing probe must leave the heartbeat stale (unhealthy), a missing or malformed
file is not "fresh", and a stopped bot clears the file instead of leaving a
fresh-looking one behind. The probe's own failure logging must not leak upstream
exception text.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.core.liveness import LivenessHeartbeat, LivenessProbe, check, main


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _heartbeat(tmp: str, clock: _Clock, *, max_age: float = 180.0) -> LivenessHeartbeat:
    return LivenessHeartbeat(
        Path(tmp) / "health" / "heartbeat", max_age_sec=max_age, clock=clock
    )


class HeartbeatFileTests(unittest.TestCase):
    def test_touch_creates_the_file_and_reports_its_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            heartbeat = _heartbeat(tmp, clock)
            self.assertIsNone(heartbeat.age_sec())
            self.assertFalse(heartbeat.is_fresh())

            heartbeat.touch()
            self.assertTrue(heartbeat.path.is_file())
            self.assertEqual(heartbeat.age_sec(), 0.0)
            self.assertTrue(heartbeat.is_fresh())

            clock.advance(179.0)
            self.assertTrue(heartbeat.is_fresh())
            clock.advance(2.0)
            self.assertFalse(heartbeat.is_fresh())

    def test_malformed_or_empty_file_is_not_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            heartbeat = _heartbeat(tmp, clock)
            heartbeat.path.parent.mkdir(parents=True, exist_ok=True)
            for content in ("", "not-a-number\n", "\n"):
                with self.subTest(content=content):
                    heartbeat.path.write_text(content, encoding="utf-8")
                    self.assertIsNone(heartbeat.age_sec())
                    self.assertFalse(heartbeat.is_fresh())

    def test_touch_is_atomic_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            heartbeat = _heartbeat(tmp, clock)
            heartbeat.touch()
            heartbeat.touch()
            siblings = sorted(path.name for path in heartbeat.path.parent.iterdir())
            self.assertEqual(siblings, [heartbeat.path.name])

    def test_clear_removes_the_file_and_tolerates_a_missing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = _heartbeat(tmp, _Clock())
            heartbeat.touch()
            heartbeat.clear()
            self.assertFalse(heartbeat.path.exists())
            heartbeat.clear()  # idempotent

    def test_non_positive_max_age_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _heartbeat(tmp, _Clock(), max_age=0.0)


class ProbeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_probe_beats_and_failure_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            heartbeat = _heartbeat(tmp, clock)
            calls = {"ok": 0}

            async def probe():
                calls["ok"] += 1
                if calls["ok"] > 1:
                    raise RuntimeError("SECRET_UPSTREAM_PAYLOAD")

            liveness = LivenessProbe(heartbeat, interval_sec=0.02, probe=probe)
            task = asyncio.create_task(liveness.run())
            try:
                for _ in range(100):
                    if heartbeat.path.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(heartbeat.is_fresh())

                # Second probe fails: the heartbeat must stop moving, so it goes
                # stale once more than max_age has passed since the last success.
                clock.advance(200.0)
                for _ in range(100):
                    if calls["ok"] >= 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertGreaterEqual(calls["ok"], 2)
                await asyncio.sleep(0.05)
                self.assertFalse(heartbeat.is_fresh())
                self.assertGreater(heartbeat.age_sec() or 0.0, 180.0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_probe_failure_is_logged_without_exception_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            heartbeat = _heartbeat(tmp, clock)

            async def probe():
                raise RuntimeError("SECRET_UPSTREAM_PAYLOAD")

            liveness = LivenessProbe(heartbeat, interval_sec=1.0, probe=probe)
            logger = logging.getLogger("app.core.liveness")
            try:
                with self.assertLogs(logger, level="WARNING") as captured:
                    task = asyncio.create_task(liveness.run())
                    for _ in range(50):
                        if captured.output:
                            break
                        await asyncio.sleep(0.01)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            finally:
                pass
            line = "\n".join(captured.output)
            self.assertIn("liveness probe failed", line)
            self.assertIn("RuntimeError", line)
            self.assertNotIn("SECRET_UPSTREAM_PAYLOAD", line)
            self.assertFalse(heartbeat.is_fresh())

    async def test_cancellation_ends_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = _heartbeat(tmp, _Clock())

            async def probe():
                return None

            liveness = LivenessProbe(heartbeat, interval_sec=30.0, probe=probe)
            task = asyncio.create_task(liveness.run())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    def test_interval_above_max_age_is_rejected_at_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = _heartbeat(tmp, _Clock(), max_age=10.0)
            with self.assertRaises(ValueError):
                LivenessProbe(heartbeat, interval_sec=11.0, probe=None)


class SettingsWiringTests(unittest.TestCase):
    def test_defaults_are_consistent_with_the_healthcheck(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.liveness_heartbeat_file, "/tmp/fcai-liveness")
        self.assertLessEqual(settings.liveness_interval_sec, settings.liveness_max_age_sec)

    def test_interval_above_max_age_fails_configuration(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                liveness_interval_sec=200,
                liveness_max_age_sec=180,
            )

    def test_check_reads_the_configured_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb"
            settings = Settings(
                _env_file=None,
                liveness_heartbeat_file=str(path),
                liveness_max_age_sec=60,
            )
            self.assertEqual(check(settings), 1)  # missing heartbeat is unhealthy
            LivenessHeartbeat(path, max_age_sec=60).touch()
            self.assertEqual(check(settings), 0)


class CommandLineTests(unittest.TestCase):
    def test_cli_reports_fresh_stale_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock_target = Path(tmp) / "hb"
            LivenessHeartbeat(clock_target, max_age_sec=60).touch()
            self.assertEqual(
                main(["--file", str(clock_target), "--max-age-sec", "60"]), 0
            )

            stale = Path(tmp) / "stale"
            stale.write_text("1000.0\n", encoding="utf-8")
            self.assertEqual(main(["--file", str(stale), "--max-age-sec", "1"]), 1)

            self.assertEqual(
                main(["--file", str(Path(tmp) / "absent"), "--max-age-sec", "60"]), 1
            )

    def test_cli_rejects_a_configuration_it_cannot_use(self):
        self.assertEqual(main(["--file", "/tmp/x", "--max-age-sec", "0"]), 1)


if __name__ == "__main__":
    unittest.main()
