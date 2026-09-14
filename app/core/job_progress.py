"""Small event payloads: no prompts, source content or model reasoning."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    label: str
    ok: bool | None = None
