"""Structural contract shared by all AI provider targets."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import ChatRequest, ChatResponse
from .health import ProviderHealth
from .target import TargetSpec


@runtime_checkable
class AIProvider(Protocol):
    spec: TargetSpec
    health: ProviderHealth

    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def aclose(self) -> None:
        ...
