"""Pure request-scoped provider retry state regressions."""

from __future__ import annotations

import unittest

from app.ai.base import ProviderError
from app.ai.retry import (
    ProviderRetryPolicy,
    RequestRetryState,
    is_cyclic_retryable_transport,
)


def transport_error(kind: str) -> ProviderError:
    error = ProviderError(f"transport failure: {kind}", transient=True)
    error.transport_kind = kind
    return error


class ProviderRetryStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ProviderRetryPolicy(
            max_consecutive_failures=2,
            max_failures_per_provider=3,
            max_failures_per_request=5,
        )
        self.state = RequestRetryState(self.policy)

    def test_success_resets_consecutive_but_not_cumulative(self) -> None:
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout")
        )
        self.state.record_chat_success("chainnode")

        slot = self.state.provider_state("chainnode")
        self.assertEqual(slot.consecutive_transport_failures, 0)
        self.assertEqual(slot.total_transport_failures, 1)
        self.assertEqual(self.state.total_transport_failures, 1)

    def test_provider_blocks_after_consecutive_limit(self) -> None:
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout")
        )
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout")
        )

        self.assertFalse(self.state.can_attempt("chainnode"))

    def test_provider_blocks_after_cumulative_limit_even_after_successes(self) -> None:
        for _ in range(2):
            self.state.record_transport_failure(
                "chainnode", transport_error("read_timeout")
            )
            self.state.record_chat_success("chainnode")

        self.assertTrue(self.state.can_attempt("chainnode"))
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout")
        )
        self.assertFalse(self.state.can_attempt("chainnode"))

    def test_request_budget_stops_all_further_transport_attempts(self) -> None:
        for name in ["a", "b", "a", "b", "a"]:
            self.state.record_transport_failure(name, transport_error("read_timeout"))
            self.state.record_chat_success(name)

        self.assertTrue(self.state.request_transport_budget_exhausted())
        self.assertFalse(self.state.can_attempt("a"))
        self.assertFalse(self.state.can_attempt("b"))

    def test_success_clears_pending_health_error_for_only_that_provider(self) -> None:
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout")
        )
        self.state.record_transport_failure("bai", transport_error("read_timeout"))

        self.state.record_chat_success("chainnode")

        self.assertIsNone(
            self.state.provider_state("chainnode").pending_health_error
        )
        self.assertIsNotNone(self.state.provider_state("bai").pending_health_error)

    def test_retryable_classification_is_narrow(self) -> None:
        for kind in ("connect_error", "connect_timeout", "read_timeout"):
            with self.subTest(kind=kind):
                self.assertTrue(is_cyclic_retryable_transport(transport_error(kind)))

        for kind in ("write_timeout", "pool_timeout"):
            with self.subTest(kind=kind):
                self.assertFalse(is_cyclic_retryable_transport(transport_error(kind)))


if __name__ == "__main__":
    unittest.main()
