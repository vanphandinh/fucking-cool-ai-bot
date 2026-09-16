"""Adversarial target-pool routing regressions.

These tests intentionally exercise public router behavior rather than the
implementation details of target identity/health.  They are the executable
contract for the provider-target-pool checkpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.ai.base import AllProvidersFailed, ChatResponse, ProviderError, ToolCall
from app.ai.router import AIProviderRouter, CompletionResult
from tests.provider_fakes import ScriptedProvider, fetch_url_tool, noop_tool


def _target(
    provider: ScriptedProvider,
    *,
    model: str,
    credential_id: str = "cred-1",
    target_id: str | None = None,
) -> ScriptedProvider:
    provider.model = model
    provider.credential_id = credential_id
    provider.target_id = target_id or (
        f"{provider.name}:{provider.capabilities.route}:{model}:{credential_id}"
    )
    return provider


def _router(
    providers: list[ScriptedProvider],
    *,
    text_order: tuple[str, ...] = ("chainnode", "xkiro"),
    vision_order: tuple[str, ...] = ("chainnode", "xkiro"),
    recovery_hops: int = 5,
) -> AIProviderRouter:
    policy = SimpleNamespace(
        max_consecutive_failures=2,
        max_failures_per_provider=3,
        max_failures_per_request=5,
        max_recovery_hops_per_request=recovery_hops,
    )
    try:
        return AIProviderRouter(
            providers,
            text_provider_order=text_order,
            vision_provider_order=vision_order,
            retry_policy=policy,
        )
    except ValueError as exc:
        self_message = f"router must allow distinct sibling targets: {exc}"
        raise AssertionError(self_message) from exc


def _messages() -> list[dict]:
    return [{"role": "user", "content": "hello"}]


def _tool_call(*, private: str = "PRIVATE-A") -> ChatResponse:
    return ChatResponse(
        tool_calls=[
            ToolCall(
                id="call_fetch",
                name="fetch_url",
                arguments={"url": "https://example.com"},
                extra_content={"provider_private": private},
            )
        ],
        assistant_metadata={"reasoning_content": private},
    )


class ProviderTargetRotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_chainnode_429_rotates_model_without_provider_fallback(self) -> None:
        model_a = _target(
            ScriptedProvider(
                "chainnode",
                [ProviderError("quota", status_code=429, retry_after=60, transient=True)],
            ),
            model="cn-model-a",
            target_id="chainnode:text:m1:c1",
        )
        model_b = _target(
            ScriptedProvider("chainnode", [ChatResponse(content="model-b")]),
            model="cn-model-b",
            target_id="chainnode:text:m2:c1",
        )
        xkiro = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="unused")]),
            model="x-model",
            target_id="xkiro:text:m1:c1",
        )
        router = _router([model_a, model_b, xkiro])

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("model-b", "chainnode", ()))
        self.assertEqual(len(model_a.calls), 1)
        self.assertEqual(len(model_b.calls), 1)
        self.assertEqual(xkiro.calls, [])

    async def test_chainnode_two_429s_then_falls_back_to_xkiro(self) -> None:
        a = _target(
            ScriptedProvider(
                "chainnode", [ProviderError("quota-a", status_code=429, transient=True)]
            ),
            model="cn-a",
            target_id="chainnode:text:m1:c1",
        )
        b = _target(
            ScriptedProvider(
                "chainnode", [ProviderError("quota-b", status_code=429, transient=True)]
            ),
            model="cn-b",
            target_id="chainnode:text:m2:c1",
        )
        xkiro = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="fallback")]),
            model="x-model",
            target_id="xkiro:text:m1:c1",
        )
        router = _router([a, b, xkiro])

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "xkiro", ("xkiro",)))
        self.assertEqual(len(a.calls), 1)
        self.assertEqual(len(b.calls), 1)
        self.assertEqual(len(xkiro.calls), 1)

    async def test_xkiro_429_rotates_credential_before_model(self) -> None:
        key1 = _target(
            ScriptedProvider(
                "xkiro", [ProviderError("quota", status_code=429, transient=True)]
            ),
            model="x-model-a",
            credential_id="cred-1",
            target_id="xkiro:text:m1:c1",
        )
        key2 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="same-model-key2")]),
            model="x-model-a",
            credential_id="cred-2",
            target_id="xkiro:text:m1:c2",
        )
        model_b_key1 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="wrong-order")]),
            model="x-model-b",
            credential_id="cred-1",
            target_id="xkiro:text:m2:c1",
        )
        router = _router(
            [key1, key2, model_b_key1],
            text_order=("xkiro",),
            vision_order=(),
        )

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("same-model-key2", "xkiro", ()))
        self.assertEqual(len(key1.calls), 1)
        self.assertEqual(len(key2.calls), 1)
        self.assertEqual(model_b_key1.calls, [])

    async def test_xkiro_401_disables_credential_across_text_and_vision(self) -> None:
        text_key1 = _target(
            ScriptedProvider(
                "xkiro",
                [ProviderError("auth", status_code=401, transient=False)],
            ),
            model="x-text",
            credential_id="cred-1",
            target_id="xkiro:text:m1:c1",
        )
        text_key2 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="text-ok")]),
            model="x-text",
            credential_id="cred-2",
            target_id="xkiro:text:m1:c2",
        )
        vision_key1 = _target(
            ScriptedProvider(
                "xkiro",
                [ChatResponse(content="must-not-run")],
                route="vision",
                max_images=1,
            ),
            model="x-vision",
            credential_id="cred-1",
            target_id="xkiro:vision:m1:c1",
        )
        vision_key2 = _target(
            ScriptedProvider(
                "xkiro",
                [ChatResponse(content="vision-ok")],
                route="vision",
                max_images=1,
            ),
            model="x-vision",
            credential_id="cred-2",
            target_id="xkiro:vision:m1:c2",
        )
        router = _router(
            [text_key1, text_key2, vision_key1, vision_key2],
            text_order=("xkiro",),
            vision_order=("xkiro",),
        )

        text = await router.complete(_messages(), None, noop_tool)
        vision = await router.complete(
            _messages(), None, noop_tool, requires_vision=True, image_count=1
        )

        self.assertEqual(text.content, "text-ok")
        self.assertEqual(vision.content, "vision-ok")
        self.assertEqual(vision_key1.calls, [])
        self.assertEqual(len(vision_key2.calls), 1)

    async def test_chainnode_503_falls_back_without_sibling_model_spray(self) -> None:
        a = _target(
            ScriptedProvider(
                "chainnode", [ProviderError("down", status_code=503, transient=True)]
            ),
            model="cn-a",
            target_id="chainnode:text:m1:c1",
        )
        sibling = _target(
            ScriptedProvider("chainnode", [ChatResponse(content="must-not-run")]),
            model="cn-b",
            target_id="chainnode:text:m2:c1",
        )
        xkiro = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="fallback")]),
            model="x-model",
            target_id="xkiro:text:m1:c1",
        )
        router = _router([a, sibling, xkiro])

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "xkiro", ("xkiro",)))
        self.assertEqual(sibling.calls, [])

    async def test_xkiro_503_falls_back_without_sibling_credential_spray(self) -> None:
        key1 = _target(
            ScriptedProvider(
                "xkiro", [ProviderError("down", status_code=503, transient=True)]
            ),
            model="x-model",
            credential_id="cred-1",
            target_id="xkiro:text:m1:c1",
        )
        key2 = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="must-not-run")]),
            model="x-model",
            credential_id="cred-2",
            target_id="xkiro:text:m1:c2",
        )
        backup = _target(
            ScriptedProvider("backup", [ChatResponse(content="backup")]),
            model="backup-model",
            target_id="backup:text:m1:c1",
        )
        router = _router(
            [key1, key2, backup],
            text_order=("xkiro", "backup"),
            vision_order=(),
        )

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("backup", "backup", ("backup",)))
        self.assertEqual(key2.calls, [])

    async def test_transport_retry_token_does_not_multiply_with_siblings(self) -> None:
        first = _target(
            ScriptedProvider(
                "chainnode",
                [
                    ProviderError("connect-1", transient=True),
                    ProviderError("connect-2", transient=True),
                ],
            ),
            model="cn-a",
            target_id="chainnode:text:m1:c1",
        )
        first.script[0].transport_kind = "connect_timeout"
        first.script[1].transport_kind = "connect_timeout"
        siblings = [
            _target(
                ScriptedProvider("chainnode", [ChatResponse(content="must-not-run")]),
                model=f"cn-{index}",
                target_id=f"chainnode:text:m{index}:c1",
            )
            for index in range(2, 8)
        ]
        xkiro = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="fallback")]),
            model="x-model",
            target_id="xkiro:text:m1:c1",
        )
        router = _router([first, *siblings, xkiro])

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result.provider, "xkiro")
        self.assertEqual(len(first.calls), 2)
        self.assertTrue(all(not sibling.calls for sibling in siblings))

    async def test_twenty_targets_are_bounded_by_recovery_hop_limit(self) -> None:
        targets = [
            _target(
                ScriptedProvider(
                    "chainnode",
                    [ProviderError("quota", status_code=429, transient=True)],
                ),
                model=f"cn-{index}",
                target_id=f"chainnode:text:m{index}:c1",
            )
            for index in range(20)
        ]
        router = _router(
            targets,
            text_order=("chainnode",),
            vision_order=(),
            recovery_hops=5,
        )

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), None, noop_tool)

        self.assertEqual(sum(len(target.calls) for target in targets), 6)

    async def test_tool_is_not_rerun_when_429_switches_target(self) -> None:
        a = _target(
            ScriptedProvider(
                "chainnode",
                [
                    _tool_call(),
                    ProviderError("quota", status_code=429, transient=True),
                ],
            ),
            model="cn-a",
            target_id="chainnode:text:m1:c1",
        )
        b = _target(
            ScriptedProvider("chainnode", [ChatResponse(content="final")]),
            model="cn-b",
            target_id="chainnode:text:m2:c1",
        )
        router = _router([a, b], text_order=("chainnode",), vision_order=())
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "PORTABLE-EVIDENCE"

        result = await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(result, CompletionResult("final", "chainnode", ()))
        self.assertEqual(tool_invocations, 1)
        self.assertIn("PORTABLE-EVIDENCE", repr(b.calls[0][0]))
        self.assertNotIn("PRIVATE-A", repr(b.calls[0][0]))

    async def test_rotation_does_not_reset_tool_budget(self) -> None:
        a = _target(
            ScriptedProvider(
                "chainnode",
                [
                    ChatResponse(
                        tool_calls=[
                            ToolCall(
                                id=f"call_{index}",
                                name="fetch_url",
                                arguments={"url": f"https://example.com/{index}"},
                            )
                            for index in range(8)
                        ]
                    ),
                    ProviderError("quota", status_code=429, transient=True),
                ],
            ),
            model="cn-a",
            target_id="chainnode:text:m1:c1",
        )
        b = _target(
            ScriptedProvider(
                "chainnode",
                [ChatResponse(content="bounded synthesis")],
            ),
            model="cn-b",
            target_id="chainnode:text:m2:c1",
        )
        router = _router([a, b], text_order=("chainnode",), vision_order=())
        executed = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal executed
            executed += 1
            return f"evidence-{executed}"

        result = await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(result.content, "bounded synthesis")
        self.assertEqual(executed, 8)
        self.assertIsNone(b.calls[0][1])


if __name__ == "__main__":
    unittest.main()
