"""Protocol constructor contract for AI wire drivers."""

from __future__ import annotations

from typing import Protocol

from ..provider import AIProvider
from ..target import TargetSpec


class Driver(Protocol):
    id: str

    def build_target(self, spec: TargetSpec, *, credential: str) -> AIProvider:
        ...
