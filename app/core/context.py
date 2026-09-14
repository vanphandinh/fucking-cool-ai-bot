"""Bộ nhớ hội thoại ngắn hạn theo từng conversation scope."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

ConversationKey = int | tuple[int, int]


class ChatMemory:
    """Giữ N lượt hỏi-đáp gần nhất cho mỗi chat hoặc forum topic, trong RAM."""

    def __init__(self, max_turns_per_chat: int = 10) -> None:
        self._max = max_turns_per_chat
        self._sequence = 0
        self._store: dict[ConversationKey, list[dict[str, Any]]] = defaultdict(list)

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    @staticmethod
    def _all_keyed(entries: list[dict[str, Any]]) -> bool:
        return bool(entries) and all(entry.get("_order_key") is not None for entry in entries)

    @staticmethod
    def _sort_keyed(entries: list[dict[str, Any]]) -> None:
        entries.sort(
            key=lambda entry: (
                int(entry["_order_key"]),
                int(entry.get("_order_part", 0)),
            )
        )

    def _prune(self, conversation_key: ConversationKey) -> None:
        entries = self._store[conversation_key]
        if self._all_keyed(entries):
            self._sort_keyed(entries)
        max_entries = self._max * 2
        if len(entries) > max_entries:
            del entries[:-max_entries]

    def push(self, conversation_key: ConversationKey, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        self._store[conversation_key].append(
            {
                "role": role,
                "content": content[:4000],
                "_sequence": self._next_sequence(),
                "_order_key": None,
                "_order_part": 0,
            }
        )
        self._prune(conversation_key)

    def push_exchange(
        self,
        conversation_key: ConversationKey,
        user_content: str,
        assistant_content: str,
        *,
        order_key: int | None = None,
    ) -> None:
        """Commit one delivered exchange without an await point between its halves."""
        user = (user_content or "").strip()
        assistant = (assistant_content or "").strip()
        if not user or not assistant:
            return
        entries = self._store[conversation_key]
        sequence = self._next_sequence()
        entries.append(
            {
                "role": "user",
                "content": user[:4000],
                "_sequence": sequence,
                "_order_key": order_key,
                "_order_part": 0,
            }
        )
        entries.append(
            {
                "role": "assistant",
                "content": assistant[:4000],
                "_sequence": sequence,
                "_order_key": order_key,
                "_order_part": 1,
            }
        )
        self._prune(conversation_key)

    def history_for(
        self,
        conversation_key: ConversationKey,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        """Trả về danh sách ({role, content}) từ cũ tới mới."""
        if limit is None:
            limit = self._max
        if limit <= 0:
            return []
        entries = list(self._store.get(conversation_key, []))
        if self._all_keyed(entries):
            self._sort_keyed(entries)
        if len(entries) > limit * 2:
            entries = entries[-(limit * 2) :]
        return [
            {"role": str(entry["role"]), "content": str(entry["content"])}
            for entry in entries
        ]
