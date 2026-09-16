"""Regression tests for scoped provider-resource recovery.

These tests intentionally use only the public router behavior that existed before
this fix so the RED commit demonstrates the audited failures without depending
on the implementation chosen for scoped-health effects.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.ai.base import ChatResponse, ProviderError
from app.ai.router import AIProviderRouter
from tests.provider_fakes import ScriptedProvider, noop_tool


def _target(
    provider: ScriptedProvider,
    *,
    model: str,
    credential_id: str,
    target_id: str,
) -> ScriptedProvider:
    provider.model = model
    provider.credential_id = credential_id
    provider.target_id = target_id
    return provider


def _router(
    providers: list[ScriptedProvider],
    *,
    text_order: tuple[str, ...],
    recovery_hops: int = 5,
) -> AIProviderRouter:
    policy = SimpleNamespace(
        max_consecutive_failures=2,
        max_failures_per_provider=3,
        max_failures_per_request=5,
        max_recovery_hops_per_request=recovery_hops,
    )
    return AIProviderRouter(
        providers,
        text_provider_order=text_order,
        vision_provider_order=(),
        retry_policy=policy,
    )


def _messages() -> list[dict]:
    return [{"role": "user", "content": "hello"}]


class ProviderRecoveryScopeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_xkiro_403_rotates_entitlement_without_family_fallback(self) -> None:
        denied = _target(
            ScriptedProvider(
                "xkiro",
                [ProviderError("entitlement", status_code=403, transient=False)],
            ),
            model="model-a",
            credential_id="cred-1",
            target_id="xkiro:text:model-a:cred-1",
        )
        sibling = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="xkiro-ok")]),
            model="model-a",
            credential_id="cred-2",
            target_id="xkiro:text:model-a:cred-2",
        )
        backup = _target(
            ScriptedProvider("backup", [ChatResponse(content="backup")]),
            model="backup-model",
            credential_id="cred-1",
            target_id="backup:text:model:cred-1",
        )
        router = _router(
            [denied, sibling, backup],
            text_order=("xkiro", "backup"),
        )

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result.content, "xkiro-ok")
        self.assertEqual(result.provider, "xkiro")
        self.assertEqual(result.fallbacks, ())
        self.assertEqual(result.target_rotations, 1)
        self.assertEqual(result.model_rotations, 0)
        self.assertEqual(result.credential_failovers, 1)
        self.assertEqual(len(denied.calls), 1)
        self.assertEqual(len(sibling.calls), 1)
        self.assertEqual(backup.calls, [])

    async def test_xkiro_402_disables_credential_across_requests(self) -> None:
        key1 = _target(
            ScriptedProvider(
                "xkiro",
                [
                    ProviderError("payment", status_code=402, transient=False),
                    ChatResponse(content="must-not-run"),
                ],
            ),
            model="model-a",
            credential_id="cred-1",
            target_id="xkiro:text:model-a:cred-1",
        )
        key2 = _target(
            ScriptedProvider(
                "xkiro",
                [ChatResponse(content="first-ok"), ChatResponse(content="second-ok")],
            ),
            model="model-a",
            credential_id="cred-2",
            target_id="xkiro:text:model-a:cred-2",
        )
        router = _router([key1, key2], text_order=("xkiro",))

        first = await router.complete(_messages(), None, noop_tool)
        second = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(first.content, "first-ok")
        self.assertEqual(second.content, "second-ok")
        self.assertEqual(len(key1.calls), 1)
        self.assertEqual(len(key2.calls), 2)

    async def test_404_disables_model_across_credential_siblings(self) -> None:
        model_a_key1 = _target(
            ScriptedProvider(
                "xkiro",
                [ProviderError("missing", status_code=404, transient=False)],
            ),
            model="model-a",
            credential_id="cred-1",
            target_id="xkiro:text:model-a:cred-1",
        )
        model_a_key2 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="must-not-run")]),
            model="model-a",
            credential_id="cred-2",
            target_id="xkiro:text:model-a:cred-2",
        )
        model_b_key1 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="model-b-ok")]),
            model="model-b",
            credential_id="cred-1",
            target_id="xkiro:text:model-b:cred-1",
        )
        router = _router(
            [model_a_key1, model_a_key2, model_b_key1],
            text_order=("xkiro",),
        )

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result.content, "model-b-ok")
        self.assertEqual(len(model_a_key1.calls), 1)
        self.assertEqual(model_a_key2.calls, [])
        self.assertEqual(len(model_b_key1.calls), 1)
        self.assertEqual(result.target_rotations, 1)
        self.assertEqual(result.model_rotations, 1)

    async def test_invalid_model_siblings_do_not_consume_recovery_hops(self) -> None:
        invalid = [
            _target(
                ScriptedProvider(
                    "xkiro",
                    [ProviderError("missing", status_code=404, transient=False)],
                ),
                model="model-a",
                credential_id=f"cred-{index}",
                target_id=f"xkiro:text:model-a:cred-{index}",
            )
            for index in range(1, 7)
        ]
        healthy = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="healthy")]),
            model="model-b",
            credential_id="cred-1",
            target_id="xkiro:text:model-b:cred-1",
        )
        router = _router(
            [*invalid, healthy],
            text_order=("xkiro",),
            recovery_hops=2,
        )

        try:
            result = await router.complete(_messages(), None, noop_tool)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"healthy model should remain reachable within hop budget: {exc}")

        self.assertEqual(result.content, "healthy")
        self.assertEqual(sum(len(target.calls) for target in invalid), 1)
        self.assertEqual(len(healthy.calls), 1)


if __name__ == "__main__":
    unittest.main()
