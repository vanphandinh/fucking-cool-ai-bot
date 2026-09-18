"""Built-in AI protocol driver registry."""

from __future__ import annotations

from .base import Driver
from .openai_chat import OpenAIChatDriver

_OPENAI_CHAT_DRIVER = OpenAIChatDriver()
DRIVERS: dict[str, Driver] = {_OPENAI_CHAT_DRIVER.id: _OPENAI_CHAT_DRIVER}


def get_driver(driver_id: str) -> Driver:
    try:
        return DRIVERS[driver_id]
    except KeyError as exc:
        raise ValueError(f"unknown AI driver: {driver_id}") from exc
