"""Trích 1 frame JPEG đại diện từ bytes video bằng ffmpeg hệ thống.

Chỉ chạy khi user đã mention/tag bot (filter quyết định ở tầng router),
module này không tự kích hoạt bất kỳ xử lý nào. Thiếu ffmpeg hoặc lỗi
-> raise để caller fallback sang thumbnail có sẵn của Telegram.
"""

from __future__ import annotations

import asyncio
import shutil

_FFMPEG_BIN = "ffmpeg"


class VideoFrameError(Exception):
    pass


def ffmpeg_available() -> bool:
    return shutil.which(_FFMPEG_BIN) is not None


def _seek_seconds(duration_sec: float | None) -> float:
    if duration_sec is None or duration_sec <= 0:
        return 1.0
    # Frame giữa video đại diện nội dung tốt hơn frame đầu với cùng 1 frame.
    return max(0.0, duration_sec / 2.0)


async def extract_middle_frame(
    video_bytes: bytes,
    *,
    duration_sec: float | None = None,
    timeout_sec: float = 15.0,
    max_width: int = 1280,
) -> bytes:
    """Trả về bytes JPEG của 1 frame. Raise VideoFrameError khi không làm được."""
    if not video_bytes:
        raise VideoFrameError("empty video")
    if not ffmpeg_available():
        raise VideoFrameError("ffmpeg missing")
    seek = _seek_seconds(duration_sec)
    cmd = [
        _FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{seek:.2f}",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-vf",
        f"scale={max_width}:-2",
        "-f",
        "mjpeg",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(video_bytes), timeout=timeout_sec
            )
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise VideoFrameError("ffmpeg timeout") from exc
        if proc.returncode != 0 or not stdout:
            raise VideoFrameError((stderr or b"ffmpeg failed")[:200].decode("utf-8", "ignore"))
        return stdout
    except FileNotFoundError as exc:
        raise VideoFrameError("ffmpeg missing") from exc
