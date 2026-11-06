"""Opaque, conversation-stable provider session identifiers."""

from __future__ import annotations

import hashlib
import secrets

# Per-process salt: the header must never carry a recoverable Telegram chat id.
_SALT = secrets.token_bytes(16)


def provider_session_id(conversation_key: int | tuple[int, int]) -> str:
    """Return one stable session id for a conversation.

    Every turn of the same conversation -- including tool rounds inside a turn
    and provider fallback -- reuses the same value, so upstream routing and
    prompt caching stay warm. The salt rotates on restart, which matches the
    in-memory chat history: both reset together.
    """

    if isinstance(conversation_key, tuple):
        material = ":".join(str(part) for part in conversation_key)
    else:
        material = str(conversation_key)
    digest = hashlib.blake2b(material.encode("utf-8"), key=_SALT, digest_size=16)
    return f"ses_{digest.hexdigest()}"
