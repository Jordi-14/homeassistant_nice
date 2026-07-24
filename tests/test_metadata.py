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
    assert manifest["name"] == "Nice"
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


def test_manifest_declares_observed_nice_discovery_services() -> None:
    """Test zeroconf declarations cover operational and provisioning services."""
    manifest = json.loads(
        (
            ROOT
            / "custom_components"
            / "nice_bidiwifi"
            / "manifest.json"
        ).read_text()
    )
    declarations = manifest["zeroconf"]

    assert "_nap._tcp.local." in declarations
    assert {
        declaration["type"]
        for declaration in declarations
        if isinstance(declaration, dict)
        and declaration.get("name") == "*nice*"
    } >= {
        "_mfi-config._tcp.local.",
        "_wnc-config._tcp.local.",
    }
    hap_matchers = [
        declaration
        for declaration in declarations
        if isinstance(declaration, dict)
        and declaration["type"] == "_hap._tcp.local."
    ]
    assert {
        (key, pattern)
        for matcher in hap_matchers
        for key, pattern in matcher["properties"].items()
    } == {
        ("model", "*bidiwifi*"),
        ("model", "*cu_wifi*"),
        ("model", "*it4wifi*"),
        ("md", "*bidiwifi*"),
        ("md", "*cu_wifi*"),
        ("md", "*it4wifi*"),
    }
