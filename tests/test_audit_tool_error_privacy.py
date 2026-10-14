"""No exception payload may pass the tool/provider boundary."""

import asyncio
import unittest

from app.ai.contracts import ToolCallPart
from app.ai.router import _execute_tool_batch


_SENTINEL = "super-secret-credential-should-never-be-shared"


class ToolErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_codes_and_retained_output_never_echo_private_message(self):
        failure_types = (
            (TimeoutError, "tool_timeout"),
            (ValueError, "invalid_input"),
            (ConnectionError, "tool_unavailable"),
            (RuntimeError, "tool_internal_error"),
        )
        retained = {}

        async def execute(name, args):
            if name == "success":
                return "safe result"
            exception_type = {kind.__name__: kind for kind, _ in failure_types}[name]
            raise exception_type(f"private URL?token={_SENTINEL}")

        calls = tuple(
            ToolCallPart(str(index), error_type.__name__, {"credential": _SENTINEL})
            for index, (error_type, _) in enumerate(failure_types)
        ) + (ToolCallPart("success", "success", {}),)
        with self.assertLogs("app.ai.router", level="WARNING") as logs:
            output = await _execute_tool_batch(
                calls, execute, retain=lambda i, data: retained.update({i: data})
            )
        self.assertEqual(
            output,
            [f"Tool execution failed: {code}" for _, code in failure_types]
            + ["safe result"],
        )
        self.assertEqual([retained[index] for index in range(len(calls))], output)
        self.assertNotIn(_SENTINEL, "".join(output) + "".join(logs.output))

    async def test_cancelled_batch_never_emits_result_from_pending_siblings(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        retained = {}

        async def execute(name, args):
            started.set()
            try:
                await asyncio.sleep(99)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(
            _execute_tool_batch(
                (ToolCallPart("1", "pending", {}),),
                execute,
                retain=lambda i, value: retained.update({i: value}),
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(retained, {})


if __name__ == "__main__":
    unittest.main()
