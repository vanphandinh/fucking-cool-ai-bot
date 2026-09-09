"""Structural contract shared by all AI provider adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .base import ChatResponse
from .capabilities import ProviderCapabilities
from .health import ProviderHealth


@runtime_checkable
class AIProvider(Protocol):
    name: str
    model: str
    supports_tools: bool
    capabilities: ProviderCapabilities
    health: ProviderHealth

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        ...

    async def aclose(self) -> None:
        ...
