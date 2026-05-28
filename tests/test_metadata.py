"""Tests for integration metadata files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches_hacs_metadata() -> None:
    """Test manifest and HACS metadata."""
    manifest = json.loads((ROOT / "custom_components" / "nice_bidiwifi" / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())

    assert manifest["domain"] == "nice_bidiwifi"
    assert manifest["name"] == hacs["name"]
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "local_polling"
    assert manifest["requirements"] == []
    assert hacs["homeassistant"] >= "2024.11.0"


def test_required_brand_assets_exist() -> None:
    """Test required brand assets."""
    brand_dir = ROOT / "custom_components" / "nice_bidiwifi" / "brand"

    assert (brand_dir / "icon.png").is_file()
    assert (brand_dir / "logo.png").is_file()
