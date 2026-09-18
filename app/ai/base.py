"""Protocol-independent AI provider errors and transport failure types."""

from __future__ import annotations

import re
from enum import Enum

_SAFE_PROVIDER_HTTP_ERROR_RE = re.compile(
    r"^(?P<provider>[^:\n]{1,100}) HTTP (?P<status>[1-5][0-9]{2})(?::.*)?$"
)


class TransportFailureKind(str, Enum):
    CONNECT_ERROR = "connect_error"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    WRITE_TIMEOUT = "write_timeout"
    POOL_TIMEOUT = "pool_timeout"


def _safe_provider_error_message(message: str) -> str:
    match = _SAFE_PROVIDER_HTTP_ERROR_RE.fullmatch(message)
    if match is None:
        return message
    return f"{match.group('provider')} HTTP {match.group('status')}"


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        unsupported_tools: bool = False,
        retry_without_tools: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
        transport_kind: TransportFailureKind | None = None,
    ) -> None:
        super().__init__(_safe_provider_error_message(message))
        self.unsupported_tools = unsupported_tools
        self.retry_without_tools = retry_without_tools
        self.status_code = status_code
        self.retry_after = retry_after
        self.transient = transient
        self.transport_kind = transport_kind


class AllProvidersFailed(Exception):
    def __init__(
        self,
        message: str,
        *,
        fallbacks: tuple[str, ...] = (),
        target_rotations: int = 0,
        model_rotations: int = 0,
        credential_failovers: int = 0,
    ) -> None:
        super().__init__(message)
        self.fallbacks = fallbacks
        self.target_rotations = target_rotations
        self.model_rotations = model_rotations
        self.credential_failovers = credential_failovers


class NoCapableProvider(Exception):
    pass
