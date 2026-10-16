"""Container liveness: a heartbeat the health check can read.

The bot can stay up while being unable to answer anything — Telegram unreachable,
DNS broken, or the event loop wedged. aiogram exposes no per-poll-round hook, so
the probe calls the same API the poller uses (``getMe``) on a slow interval and
touches a heartbeat file only when that call succeeds. The health check reads the
file's age, which makes a stuck process visible to ``docker compose ps`` instead of
silently healthy.

Failure states are deliberately not self-healing or self-reporting beyond the log
line: a stale heartbeat means "inspect the bot", and the restart policy decides.

CLI (used by the compose health check):
    python -m app.core.liveness            # exit 0 when fresh, 1 when stale/missing
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_FILE = "/tmp/fcai-liveness"
DEFAULT_MAX_AGE_SEC = 180.0
DEFAULT_INTERVAL_SEC = 60.0


class LivenessHeartbeat:
    """A file whose mtime is the last moment the bot answered Telegram."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_age_sec <= 0:
            raise ValueError("max_age_sec must be positive")
        self.path = Path(path)
        self.max_age_sec = float(max_age_sec)
        self._clock = clock

    def touch(self) -> None:
        """Write the current time atomically so a reader never sees a partial file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(f"{self._clock():.3f}\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def age_sec(self) -> float | None:
        """Seconds since the last beat, or ``None`` when the file is unreadable."""
        try:
            stamped = float(self.path.read_text(encoding="utf-8").strip() or "nan")
        except (OSError, ValueError):
            return None
        if stamped != stamped:  # NaN: the file exists but carries no usable time
            return None
        return max(0.0, self._clock() - stamped)

    def is_fresh(self) -> bool:
        age = self.age_sec()
        return age is not None and age <= self.max_age_sec

    def clear(self) -> None:
        """Drop the heartbeat so a stopped process cannot look alive."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class LivenessProbe:
    """Touch the heartbeat whenever the bot can still reach Telegram."""

    def __init__(
        self,
        heartbeat: LivenessHeartbeat,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        probe=None,
    ) -> None:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        if interval_sec > heartbeat.max_age_sec:
            raise ValueError("interval_sec must not exceed max_age_sec")
        self.heartbeat = heartbeat
        self.interval_sec = float(interval_sec)
        self._probe = probe

    async def run(self, bot=None) -> None:
        """Beat the heartbeat after each successful probe, until cancelled."""
        probe = self._probe
        if probe is None:
            if bot is None:
                raise ValueError("a bot or a probe callable is required")
            probe = bot.get_me
        while True:
            try:
                await probe()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - liveness must survive any error
                # No exception text: upstream messages can carry request payloads.
                logger.warning(
                    "liveness probe failed: exc_type=%s", type(exc).__name__
                )
            else:
                self.heartbeat.touch()
            await asyncio.sleep(self.interval_sec)


def build_probe(settings, bot) -> LivenessProbe:
    """Probe configured from ``Settings`` (single source of truth with the CLI)."""
    heartbeat = LivenessHeartbeat(
        settings.liveness_heartbeat_file,
        max_age_sec=settings.liveness_max_age_sec,
    )
    return LivenessProbe(heartbeat, interval_sec=settings.liveness_interval_sec)


def check(settings) -> int:
    """0 when the heartbeat is fresh, 1 when it is stale or missing."""
    heartbeat = LivenessHeartbeat(
        settings.liveness_heartbeat_file,
        max_age_sec=settings.liveness_max_age_sec,
    )
    age = heartbeat.age_sec()
    if age is None:
        print(f"liveness: no heartbeat at {heartbeat.path}")
        return 1
    if age > heartbeat.max_age_sec:
        print(
            f"liveness: heartbeat is {age:.1f}s old "
            f"(max {heartbeat.max_age_sec:.0f}s) at {heartbeat.path}"
        )
        return 1
    print(f"liveness: heartbeat {age:.1f}s old (max {heartbeat.max_age_sec:.0f}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="override LIVENESS_HEARTBEAT_FILE")
    parser.add_argument("--max-age-sec", type=float, help="override LIVENESS_MAX_AGE_SEC")
    args = parser.parse_args(argv)

    try:
        from ..config import Settings

        settings = Settings(
            _env_file=None,
            **(
                {
                    key: value
                    for key, value in (
                        ("liveness_heartbeat_file", args.file),
                        ("liveness_max_age_sec", args.max_age_sec),
                    )
                    if value is not None
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable config is unhealthy
        print(f"liveness: configuration unusable ({type(exc).__name__})")
        return 1
    return check(settings)


if __name__ == "__main__":
    raise SystemExit(main())
