"""Bộ nhớ hội thoại ngắn hạn (LRU) cho từng group."""

from __future__ import annotations

from collections import defaultdict, deque


class ChatMemory:
    """Giữ N lượt hỏi-đáp gần nhất cho mỗi group, trong RAM."""

    def __init__(self, max_turns_per_chat: int = 10) -> None:
        self._max = max_turns_per_chat
        self._store: dict[int, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_turns_per_chat * 2)
        )

    def push(self, chat_id: int, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        self._store[chat_id].append({"role": role, "content": content[:4000]})

    def history_for(self, chat_id: int, limit: int | None = None) -> list[dict[str, str]]:
        """Trả về danh sách ({role, content}) từ cũ tới mới."""
        if limit is None:
            limit = self._max
        if limit <= 0:
            return []
        entries = list(self._store.get(chat_id, []))
        # Tối đa `limit` cặp -> 2*limit bản ghi
        if len(entries) > limit * 2:
            entries = entries[-(limit * 2) :]
        return entries
