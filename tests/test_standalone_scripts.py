"""Tests for repository diagnostic scripts outside a Home Assistant install."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script",
    ["scripts/dump_bidi_capabilities.py", "scripts/probe_cuwifi_status.py"],
)
def test_script_help_works_without_site_packages(script: str) -> None:
    """Test protocol scripts do not require Home Assistant or its stubs."""
    result = subprocess.run(
        [sys.executable, "-S", script, "--help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
