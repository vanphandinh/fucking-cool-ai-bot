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

    def test_same_provider_retry_token_is_monotonic_across_success(self) -> None:
        self.assertTrue(self.state.can_retry_same("chainnode"))
        self.state.consume_same_provider_retry("chainnode")
        self.state.record_chat_success("chainnode")

        self.assertFalse(self.state.can_retry_same("chainnode"))

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
            "chainnode", transport_error("read_timeout"), target_id="chain-target"
        )
        self.state.record_transport_failure(
            "xkiro", transport_error("read_timeout"), target_id="xkiro-target"
        )

        self.state.record_chat_success("chainnode")

        self.assertEqual(self.state.pending_health_incidents("chainnode"), ())
        self.assertEqual(len(self.state.pending_health_incidents("xkiro")), 1)

    def test_pending_health_incidents_are_keyed_by_target(self) -> None:
        self.state.record_transport_failure(
            "chainnode",
            transport_error("connect_timeout"),
            health_barrier_generation=3,
            target_id="target-a",
        )
        self.state.record_transport_failure(
            "chainnode",
            transport_error("read_timeout"),
            health_barrier_generation=4,
            target_id="target-b",
        )

        incidents = self.state.pending_health_incidents("chainnode")
        self.assertEqual({incident.target_id for incident in incidents}, {"target-a", "target-b"})

    def test_same_target_transport_retries_share_one_pending_incident(self) -> None:
        self.state.record_transport_failure(
            "chainnode",
            transport_error("connect_timeout"),
            health_barrier_generation=7,
            target_id="target-a",
        )
        self.state.record_transport_failure(
            "chainnode",
            transport_error("read_timeout"),
            health_barrier_generation=8,
            target_id="target-a",
        )

        slot = self.state.family_state("chainnode")
        incidents = self.state.pending_health_incidents("chainnode")
        self.assertEqual(slot.total_transport_failures, 2)
        self.assertEqual(self.state.total_transport_failures, 2)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].health_barrier_generation, 7)
        self.assertIn("read_timeout", str(incidents[0].error))

    def test_discarding_one_target_incident_preserves_sibling_incident(self) -> None:
        self.state.record_transport_failure(
            "chainnode", transport_error("connect_timeout"), target_id="target-a"
        )
        self.state.record_transport_failure(
            "chainnode", transport_error("read_timeout"), target_id="target-b"
        )

        self.state.discard_pending_health_incident("chainnode", "target-a")

        incidents = self.state.pending_health_incidents("chainnode")
        self.assertEqual([incident.target_id for incident in incidents], ["target-b"])

    def test_retryable_classification_is_narrow(self) -> None:
        for kind in ("connect_error", "connect_timeout", "read_timeout"):
            with self.subTest(kind=kind):
                self.assertTrue(is_cyclic_retryable_transport(transport_error(kind)))

        for kind in ("write_timeout", "pool_timeout"):
            with self.subTest(kind=kind):
                self.assertFalse(is_cyclic_retryable_transport(transport_error(kind)))


if __name__ == "__main__":
    unittest.main()
