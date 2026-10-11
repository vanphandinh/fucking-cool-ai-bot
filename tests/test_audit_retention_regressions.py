"""Deterministic expiry, capacity, and ordering regressions for RAM state."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.config import Settings
from app.core.context import ChatMemory
from app.core.rate_limiter import RateLimiter


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RateLimiterRetentionTests(unittest.TestCase):
    def test_request_window_boundary_and_warning_cooldown(self):
        clock = _Clock()
        limiter = RateLimiter(2, clock=clock)
        self.assertEqual(limiter.allow(1), (True, 0.0))
        self.assertEqual(limiter.allow(1), (True, 0.0))
        self.assertEqual(limiter.allow(1), (False, 60.0))
        self.assertTrue(limiter.should_warn(1, cooldown_sec=90))
        clock.advance(60)
        self.assertEqual(limiter.allow(1), (True, 0.0))
        self.assertFalse(limiter.should_warn(1, cooldown_sec=90))
        clock.advance(30)
        self.assertTrue(limiter.should_warn(1, cooldown_sec=90))

    def test_sliding_window_exact_millisecond_boundary_and_retry(self):
        clock = _Clock()
        limiter = RateLimiter(2, clock=clock)
        self.assertEqual(limiter.allow(7), (True, 0.0))
        clock.advance(0.001)
        self.assertEqual(limiter.allow(7), (True, 0.0))
        clock.advance(59.998)
        allowed, retry_in = limiter.allow(7)
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry_in, 0.001)
        clock.advance(0.001)
        self.assertEqual(limiter.allow(7), (True, 0.0))
        self.assertEqual(limiter.allow(7)[0], False)
        clock.advance(0.001)
        self.assertEqual(limiter.allow(7), (True, 0.0))

    def test_denied_spam_does_not_extend_request_retention(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        self.assertTrue(limiter.allow(5)[0])
        for _ in range(599):
            clock.advance(0.1)
            self.assertFalse(limiter.allow(5)[0])
        clock.now = 160.0
        # The original accepted request expires even if the user kept retrying.
        limiter.should_warn(6)
        self.assertNotIn(5, limiter._stamps)
        self.assertTrue(limiter.allow(5)[0])

    def test_expire_warning_and_request_independently(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        limiter.allow(1)
        limiter.should_warn(1, cooldown_sec=120)
        clock.advance(61)
        limiter.allow(2)
        self.assertNotIn(1, limiter._stamps)
        self.assertIn(1, limiter._last_warn)
        clock.advance(60)
        limiter.allow(3)
        self.assertNotIn(1, limiter._last_warn)

    def test_cleanup_removes_all_expired_request_keys_in_one_call(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        for user_id in range(700):
            self.assertTrue(limiter.allow(user_id)[0])
        self.assertEqual(len(limiter._stamps), 700)
        clock.advance(61)
        limiter.allow(1000)
        self.assertEqual(len(limiter._stamps), 1)
        self.assertIn(1000, limiter._stamps)

    def test_sustained_churn_keeps_only_live_request_state(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        for second in range(400):
            for offset in range(100):
                self.assertTrue(limiter.allow(second * 100 + offset)[0])
            if second < 399:
                clock.advance(1)
        self.assertEqual(len(limiter._stamps), 6_000)
        clock.advance(61)
        limiter.allow(40_000)
        self.assertEqual(len(limiter._stamps), 1)

    def test_repeated_user_heap_is_compacted_and_expires_on_time(self):
        clock = _Clock()
        limiter = RateLimiter(2_500, clock=clock)
        for _ in range(2_000):
            self.assertTrue(limiter.allow(1)[0])
            clock.advance(0.001)
            self.assertLessEqual(len(limiter._expiry_heap), 2 * 1 + 64)
        clock.advance(61)
        limiter.should_warn(2)
        self.assertEqual(limiter._stamps, {})
        self.assertLessEqual(len(limiter._expiry_heap), 2 * 1 + 64)

    def test_compaction_with_mixed_warning_and_request_deadlines(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        limiter.allow(1)
        limiter.should_warn(1, cooldown_sec=120)
        for _ in range(250):
            clock.advance(60)
            limiter.allow(1)
            limiter.should_warn(1, cooldown_sec=120)
            live = len(limiter._request_expirations) + len(limiter._warning_expirations)
            self.assertLessEqual(len(limiter._expiry_heap), 2 * live + 64)
        clock.advance(61)
        limiter.allow(2)
        self.assertNotIn(1, limiter._stamps)
        self.assertIn(1, limiter._last_warn)
        clock.advance(60)
        limiter.allow(3)
        self.assertNotIn(1, limiter._last_warn)

    def test_variable_warning_cooldowns_have_independent_deadlines(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        self.assertTrue(limiter.should_warn(11, cooldown_sec=10))
        self.assertTrue(limiter.should_warn(12, cooldown_sec=90))
        clock.advance(9.999)
        self.assertFalse(limiter.should_warn(11, cooldown_sec=10))
        clock.now = 110.0
        self.assertFalse(limiter.should_warn(11, cooldown_sec=60))
        self.assertFalse(limiter.should_warn(12, cooldown_sec=90))
        clock.advance(50)
        self.assertTrue(limiter.should_warn(11, cooldown_sec=60))
        clock.now = 170.0
        self.assertFalse(limiter.should_warn(11, cooldown_sec=60))
        self.assertFalse(limiter.should_warn(12, cooldown_sec=90))
        clock.advance(20)
        self.assertTrue(limiter.should_warn(12, cooldown_sec=90))

    def test_longer_cooldown_uses_previous_warning_after_short_cooldown_expires(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        self.assertTrue(limiter.should_warn(42, cooldown_sec=10))
        clock.advance(11)
        # Another user's request performs idle cleanup before user 42 returns.
        limiter.allow(99)
        self.assertFalse(limiter.should_warn(42, cooldown_sec=90))
        clock.advance(79)
        self.assertTrue(limiter.should_warn(42, cooldown_sec=90))

    def test_suppressed_warning_extends_lookback_to_requested_cooldown(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        self.assertTrue(limiter.should_warn(42, cooldown_sec=10))
        clock.advance(119)
        self.assertFalse(limiter.should_warn(42, cooldown_sec=150))
        clock.advance(2)
        limiter.allow(99)  # expire unrelated entries
        self.assertFalse(limiter.should_warn(42, cooldown_sec=150))
        clock.advance(29)
        self.assertTrue(limiter.should_warn(42, cooldown_sec=150))

    def test_warning_history_is_bounded_under_sustained_churn(self):
        clock = _Clock()
        limiter = RateLimiter(1, clock=clock)
        for second in range(400):
            for offset in range(100):
                self.assertTrue(limiter.should_warn(second * 100 + offset))
            if second < 399:
                clock.advance(1)
        self.assertEqual(len(limiter._last_warn), 12_000)
        self.assertLessEqual(
            len(limiter._expiry_heap),
            2 * len(limiter._warning_expirations) + 64,
        )
        clock.advance(121)
        limiter.allow(40_000)
        self.assertEqual(limiter._last_warn, {})

    def test_disabled_limit_does_not_write_request_stamps(self):
        clock = _Clock()
        limiter = RateLimiter(0, clock=clock)
        self.assertEqual(limiter.allow(42), (False, 60.0))
        self.assertEqual(limiter._stamps, {})
        self.assertTrue(limiter.should_warn(42))
        clock.advance(121)
        limiter.allow(99)
        self.assertNotIn(42, limiter._last_warn)
        self.assertEqual(limiter._stamps, {})


class RateLimiterTelegramIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_and_controlled_handlers_throttle_warning_replies(self):
        from app.bot.handlers import _handle_question
        from app.bot.question_runner import submit_controlled_question

        clock = _Clock()
        limiter = RateLimiter(0, clock=clock)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=10),
            message_thread_id=None,
            bot=None,
            reply=AsyncMock(),
        )
        for _ in range(2):
            await _handle_question(
                message, "hello", None, None, None, None, limiter, None, None
            )
        self.assertEqual(message.reply.await_count, 1)
        clock.advance(30)
        await submit_controlled_question(
            message, "hello", None,
            settings=None, orchestrator=None, memory=None, limiter=limiter,
            stats=None, media_loader=None, manager=None, presenter=None,
        )
        self.assertEqual(message.reply.await_count, 2)
        self.assertIn("xin chờ", message.reply.call_args.args[0])


class ChatMemoryRetentionTests(unittest.TestCase):
    def test_cap_evicts_least_recently_read_topic_and_preserves_copies(self):
        clock = _Clock()
        memory = ChatMemory(max_turns_per_chat=2, max_conversations=2, clock=clock)
        memory.push_exchange((1, 1), "first", "a", order_key=1)
        memory.push_exchange((1, 2), "second", "b", order_key=2)
        copied = memory.history_for((1, 1))
        copied[0]["content"] = "tampered"
        memory.push_exchange(1, "third", "c", order_key=3)
        self.assertEqual(memory.history_for((1, 2)), [])
        self.assertEqual(memory.history_for((1, 1))[0]["content"], "first")
        self.assertEqual(len(memory._store), 2)

    def test_ttl_exact_boundary_and_read_refresh(self):
        clock = _Clock()
        memory = ChatMemory(max_conversations=4, ttl_sec=60, clock=clock)
        self.assertEqual(memory.history_for(9), [])
        self.assertEqual(len(memory._store), 0)
        memory.push_exchange(1, "hello", "world")
        clock.advance(59)
        self.assertEqual(len(memory.history_for(1)), 2)
        clock.advance(60)
        self.assertEqual(memory.history_for(1), [])
        self.assertNotIn(1, memory._accessed)

    def test_message_order_and_atomic_pairs_survive_lru_and_pruning(self):
        clock = _Clock()
        memory = ChatMemory(max_turns_per_chat=2, max_conversations=2, clock=clock)
        memory.push_exchange((3, 1), "newer", "reply2", order_key=20)
        memory.push_exchange((3, 1), "older", "reply1", order_key=10)
        memory.push_exchange((3, 1), "newest", "reply3", order_key=30)
        self.assertEqual(
            [entry["content"] for entry in memory.history_for((3, 1))],
            ["newer", "reply2", "newest", "reply3"],
        )
        self.assertEqual(memory.history_for((3, 2)), [])
        self.assertEqual(len(memory._store), 1)

    def test_settings_reject_invalid_retention_policy(self):
        self.assertEqual(Settings(_env_file=None).max_context_conversations, 2048)
        self.assertEqual(Settings(_env_file=None).context_ttl_sec, 86400)
        for setting in (
            {"max_context_conversations": 0},
            {"max_context_conversations": 100001},
            {"context_ttl_sec": 0},
            {"context_ttl_sec": float("nan")},
        ):
            with self.subTest(setting=setting), self.assertRaises(ValidationError):
                Settings(_env_file=None, **setting)


if __name__ == "__main__":
    unittest.main()
