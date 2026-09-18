"""PR #58 regression: shutdown must honor the generic provider protocol."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.main import _close_providers


class _FailingMinimalTarget:
    def __init__(self) -> None:
        self.spec = SimpleNamespace(
            identity=SimpleNamespace(target_id="demo:text:m1:c1")
        )
        self.health = SimpleNamespace()

    async def chat(self, _request):
        raise AssertionError("chat is not used during shutdown")

    async def aclose(self) -> None:
        raise RuntimeError("close failed")


class ProviderShutdownProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_failure_does_not_require_legacy_name_attribute(self) -> None:
        target = _FailingMinimalTarget()
        router = SimpleNamespace(providers=[target])

        with self.assertLogs("app.main", level="DEBUG") as captured:
            await _close_providers(router)

        output = "\n".join(captured.output)
        self.assertIn("demo:text:m1:c1", output)
        self.assertNotIn("close failed", output)


if __name__ == "__main__":
    unittest.main()
