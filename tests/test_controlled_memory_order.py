import unittest

from app.core.context import ChatMemory


class ControlledMemoryOrderTests(unittest.TestCase):
    def test_keyed_exchanges_follow_submission_order_not_completion_order(self):
        memory = ChatMemory(max_turns_per_chat=4)
        key = (-100, 7)

        memory.push_exchange(key, "question B", "answer B", order_key=102)
        memory.push_exchange(key, "question A", "answer A", order_key=101)

        self.assertEqual(
            memory.history_for(key, 4),
            [
                {"role": "user", "content": "question A"},
                {"role": "assistant", "content": "answer A"},
                {"role": "user", "content": "question B"},
                {"role": "assistant", "content": "answer B"},
            ],
        )

    def test_keyed_memory_evicts_oldest_submission_not_latest_completion(self):
        memory = ChatMemory(max_turns_per_chat=2)
        key = (-100, 7)

        memory.push_exchange(key, "question C", "answer C", order_key=103)
        memory.push_exchange(key, "question D", "answer D", order_key=104)
        memory.push_exchange(key, "question B", "answer B", order_key=102)

        self.assertEqual(
            memory.history_for(key, 2),
            [
                {"role": "user", "content": "question C"},
                {"role": "assistant", "content": "answer C"},
                {"role": "user", "content": "question D"},
                {"role": "assistant", "content": "answer D"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
