from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.ai.chainnode import build_chainnode_provider_slots
from app.ai.xkiro import build_xkiro_provider_slots
from app.config import Settings, XKIRO_DEFAULT_BASE_URL
import scripts.probe_xkiro as xkiro_probe

ROOT = Path(__file__).parents[1]
DESIGN_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-09-17-canonical-env-cutover-design.md"
)
SYNC_SCRIPT = ROOT / "scripts" / "sync_env.py"


def _assignments(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _design_contract_assignments() -> dict[str, str]:
    text = DESIGN_SPEC.read_text(encoding="utf-8")
    section = text.split("## Canonical environment contract", 1)[1]
    section = section.split("## Strict environment synchronization", 1)[0]
    blocks = re.findall(r"```env\n(.*?)```", section, flags=re.DOTALL)
    if len(blocks) != 2:
        raise AssertionError(
            "canonical environment contract must contain provider and retry env blocks"
        )

    assignments: dict[str, str] = {}
    for block in blocks:
        assignments.update(_assignments(block))
    return assignments


def _set_env_value(rendered: str, key: str, value: str) -> str:
    prefix = f"{key}="
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            return "\n".join(lines) + "\n"
    raise AssertionError(f"missing {key} in synced env")


def _run_sync(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SYNC_SCRIPT)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )


class ProviderEnvContractTests(unittest.TestCase):
    def test_template_publishes_canonical_provider_and_retry_contract(self) -> None:
        assignments = _assignments((ROOT / ".env.example").read_text(encoding="utf-8"))
        self.assertEqual(assignments["TEXT_PROVIDER_ORDER"], "chainnode,xkiro")
        self.assertEqual(assignments["VISION_PROVIDER_ORDER"], "chainnode,xkiro")
        self.assertEqual(assignments["XKIRO_BASE_URL"], XKIRO_DEFAULT_BASE_URL)
        self.assertEqual(
            assignments["CHAINNODE_TEXT_MODELS"],
            "cl/cline-free/deepseek-v4.1-flash",
        )
        self.assertEqual(
            assignments["CHAINNODE_VISION_MODELS"],
            "cl/cline-free/muse-spark-1.3-contributor",
        )
        self.assertEqual(assignments["PROVIDER_RETRY_MAX_CONSECUTIVE"], "2")
        self.assertEqual(assignments["PROVIDER_RETRY_MAX_PER_PROVIDER"], "3")
        self.assertEqual(assignments["PROVIDER_RETRY_MAX_PER_REQUEST"], "5")
        self.assertEqual(assignments["PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST"], "5")

    def test_design_contract_matches_template_provider_and_retry_defaults(self) -> None:
        template = _assignments((ROOT / ".env.example").read_text(encoding="utf-8"))
        design = _design_contract_assignments()
        keys = (
            "CHAINNODE_API_KEYS",
            "CHAINNODE_BASE_URL",
            "CHAINNODE_TEXT_MODELS",
            "CHAINNODE_VISION_MODELS",
            "CHAINNODE_REQUEST_TIMEOUT_SEC",
            "XKIRO_API_KEYS",
            "XKIRO_BASE_URL",
            "XKIRO_TEXT_MODELS",
            "XKIRO_VISION_MODELS",
            "XKIRO_REQUEST_TIMEOUT_SEC",
            "TEXT_PROVIDER_ORDER",
            "VISION_PROVIDER_ORDER",
            "PROVIDER_RETRY_MAX_CONSECUTIVE",
            "PROVIDER_RETRY_MAX_PER_PROVIDER",
            "PROVIDER_RETRY_MAX_PER_REQUEST",
            "PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST",
        )

        self.assertEqual(
            {key: design[key] for key in keys},
            {key: template[key] for key in keys},
        )

    def test_retry_fields_have_no_validation_aliases(self) -> None:
        for field_name in (
            "provider_retry_max_consecutive",
            "provider_retry_max_per_provider",
            "provider_retry_max_per_request",
        ):
            self.assertIsNone(Settings.model_fields[field_name].validation_alias)

    def test_fresh_template_chainnode_pool_is_operational_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                (ROOT / ".env.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            first = _run_sync(directory)
            self.assertEqual(first.returncode, 0, first.stderr)

            env_path = directory / ".env"
            rendered = env_path.read_text(encoding="utf-8")
            for key, value in (
                ("CHAINNODE_API_KEYS", "ChainOne,ChainTwo"),
                ("CHAINNODE_TEXT_MODELS", "ModelA,ModelB"),
                ("CHAINNODE_VISION_MODELS", "VisionA"),
                ("TEXT_PROVIDER_ORDER", "chainnode"),
                ("VISION_PROVIDER_ORDER", "chainnode"),
            ):
                rendered = _set_env_value(rendered, key, value)
            env_path.write_text(rendered, encoding="utf-8")

            second = _run_sync(directory)
            after_second = env_path.read_text(encoding="utf-8")
            third = _run_sync(directory)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), after_second)

            settings = Settings(_env_file=env_path)
            slots = build_chainnode_provider_slots(settings)
            try:
                self.assertEqual(
                    [(slot.capabilities.route, slot.model, slot.credential_id) for slot in slots],
                    [
                        ("text", "ModelA", "cred-1"),
                        ("text", "ModelA", "cred-2"),
                        ("text", "ModelB", "cred-1"),
                        ("text", "ModelB", "cred-2"),
                        ("vision", "VisionA", "cred-1"),
                        ("vision", "VisionA", "cred-2"),
                    ],
                )
            finally:
                for slot in slots:
                    asyncio.run(slot.aclose())

    def test_fresh_template_xkiro_pool_is_operational_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                (ROOT / ".env.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            first = _run_sync(directory)
            self.assertEqual(first.returncode, 0, first.stderr)

            env_path = directory / ".env"
            rendered = env_path.read_text(encoding="utf-8")
            for key, value in (
                ("XKIRO_API_KEYS", "KeyOne,KeyTwo"),
                ("XKIRO_TEXT_MODELS", "ModelA,ModelB"),
                ("XKIRO_VISION_MODELS", "VisionA"),
                ("TEXT_PROVIDER_ORDER", "xkiro"),
                ("VISION_PROVIDER_ORDER", "xkiro"),
            ):
                rendered = _set_env_value(rendered, key, value)
            env_path.write_text(rendered, encoding="utf-8")

            second = _run_sync(directory)
            after_second = env_path.read_text(encoding="utf-8")
            third = _run_sync(directory)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), after_second)

            settings = Settings(_env_file=env_path)
            slots = build_xkiro_provider_slots(settings)
            try:
                self.assertEqual(
                    [(slot.capabilities.route, slot.model, slot.credential_id) for slot in slots],
                    [
                        ("text", "ModelA", "cred-1"),
                        ("text", "ModelA", "cred-2"),
                        ("text", "ModelB", "cred-1"),
                        ("text", "ModelB", "cred-2"),
                        ("vision", "VisionA", "cred-1"),
                        ("vision", "VisionA", "cred-2"),
                    ],
                )
            finally:
                for slot in slots:
                    asyncio.run(slot.aclose())

    def test_runtime_uses_configured_xkiro_base_url(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_keys="secret",
            xkiro_text_models="ModelA",
            xkiro_base_url="https://xkiro-gateway.example/custom/v1",
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        slots = build_xkiro_provider_slots(settings)
        try:
            self.assertEqual(
                str(slots[0]._client.base_url),
                "https://xkiro-gateway.example/custom/v1/",
            )
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())

    def test_blank_xkiro_base_url_uses_shared_default(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_keys="secret",
            xkiro_text_models="ModelA",
            xkiro_base_url="   ",
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        slots = build_xkiro_provider_slots(settings)
        try:
            self.assertEqual(
                str(slots[0]._client.base_url),
                XKIRO_DEFAULT_BASE_URL + "/",
            )
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())

        with patch.dict(os.environ, {"XKIRO_BASE_URL": "   "}, clear=False):
            self.assertEqual(xkiro_probe.xkiro_base_url(), XKIRO_DEFAULT_BASE_URL)

    def test_probe_normalizes_configured_xkiro_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {"XKIRO_BASE_URL": "https://probe-gateway.example/v1/"},
            clear=False,
        ):
            self.assertEqual(
                xkiro_probe.xkiro_base_url(),
                "https://probe-gateway.example/v1",
            )


if __name__ == "__main__":
    unittest.main()
