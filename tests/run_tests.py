"""Canonical offline regression launcher.

The repository keeps behavior tests in ``tests/test_*.py``. This script exists
for the historical ``python tests/run_tests.py`` CI entrypoint and deliberately
runs the same structured unittest suite instead of maintaining a second,
divergent manual harness.

It additionally installs an offline tripwire: a test that reaches the public
internet fails loudly instead of silently depending on a live provider's
availability, throttling or rate limits. Loopback is still allowed so tests can
bind local servers, and ``ALLOW_LIVE_NETWORK=1`` turns the guard off for
deliberate live checks against real providers.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import unittest


ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))


class NetworkAccessDuringTests(RuntimeError):
    """Raised when a test tries to reach a non-loopback host."""


_LOOPBACK_NAMES = {"", "localhost", "::1"}


def _is_loopback(host: object) -> bool:
    text = str(host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    lowered = text.lower()
    if lowered in _LOOPBACK_NAMES:
        return True
    return lowered.startswith("127.") or lowered.startswith("::1")


def _blocked(host: object, what: str) -> NetworkAccessDuringTests:
    return NetworkAccessDuringTests(
        f"offline test suite attempted real network access to {host!r} via {what}; "
        "patch the provider/transport in the test, or set ALLOW_LIVE_NETWORK=1 "
        "for a deliberate live check"
    )


def install_socket_tripwire() -> None:
    """Block outbound TCP/DNS for non-loopback destinations.

    Catches the Python-level clients the app uses directly (httpx, requests,
    aiohttp). ``primp``-based DuckDuckGo traffic is handled separately by
    :func:`install_ddgs_tripwire`, because that client does its networking
    inside a Rust extension and never touches ``socket``.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            raise _blocked(host, "DNS resolution")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise _blocked(address[0], "socket connect")
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise _blocked(address[0], "socket connect_ex")
        return real_connect_ex(self, address, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex


def install_ddgs_tripwire() -> None:
    """Block the real ``ddgs`` client, which bypasses ``socket``.

    Tests that exercise the DDGS backend patch ``ddgs_backend.search_ddgs``
    (or swap ``ddgs`` in ``sys.modules``), so the real class is only reached
    when a test forgets to isolate itself — exactly the case worth failing.
    """
    try:
        import ddgs
    except Exception:  # pragma: no cover - optional dependency
        return

    class _BlockedDDGS:
        def __init__(self, *args, **kwargs):
            raise _blocked("ddgs", "the real DDGS client")

    ddgs.DDGS = _BlockedDDGS


def install_network_tripwire() -> bool:
    """Install every guard unless the operator opted into live network."""
    if os.environ.get("ALLOW_LIVE_NETWORK") == "1":
        return False
    install_socket_tripwire()
    install_ddgs_tripwire()
    return True


def main() -> int:
    guarded = install_network_tripwire()
    print(
        "network tripwire: "
        + ("active (offline suite)" if guarded else "disabled by ALLOW_LIVE_NETWORK=1"),
        flush=True,
    )
    suite = unittest.defaultTestLoader.discover(str(TESTS), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
