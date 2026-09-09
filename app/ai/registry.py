"""Generic provider-family registry and configured slot construction."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..config import Settings
from .aurora import build_aurora_provider_slots
from .bai import build_bai_provider_slots
from .provider import AIProvider

ProviderFactory = Callable[[Settings], list[AIProvider]]

PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "bai": build_bai_provider_slots,
    "aurora": build_aurora_provider_slots,
}


def build_registered_providers(
    settings: Settings,
    provider_names: Sequence[str],
) -> list[AIProvider]:
    names = tuple(
        dict.fromkeys(
            str(raw).strip().lower()
            for raw in provider_names
            if str(raw).strip()
        )
    )
    unknown = [name for name in names if name not in PROVIDER_FACTORIES]
    if unknown:
        raise ValueError("AI provider chưa được đăng ký: " + ", ".join(unknown))

    providers: list[AIProvider] = []
    for name in names:
        slots = PROVIDER_FACTORIES[name](settings)
        for slot in slots:
            if slot.name != name:
                raise ValueError(
                    f"Provider factory {name!r} trả slot sai provider key: {slot.name!r}"
                )
        providers.extend(slots)
    return providers
