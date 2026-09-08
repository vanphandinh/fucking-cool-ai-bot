"""Bộ nhớ hội thoại ngắn hạn theo từng conversation scope."""

from __future__ import annotations

from collections import defaultdict, deque

ConversationKey = int | tuple[int, int]


class ChatMemory:
    """Giữ N lượt hỏi-đáp gần nhất cho mỗi chat hoặc forum topic, trong RAM."""

    def __init__(self, max_turns_per_chat: int = 10) -> None:
        self._max = max_turns_per_chat
        self._store: dict[ConversationKey, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_turns_per_chat * 2)
        )

    def push(self, conversation_key: ConversationKey, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        self._store[conversation_key].append({"role": role, "content": content[:4000]})

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
        # Tối đa `limit` cặp -> 2*limit bản ghi
        if len(entries) > limit * 2:
            entries = entries[-(limit * 2) :]
        return entries
