"""Tests for Home Assistant translation files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "nice_bidiwifi"
STRINGS_FILE = INTEGRATION_DIR / "strings.json"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _flatten_keys(value: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(value, dict):
        return {prefix}

    keys: set[tuple[str, ...]] = set()
    for child_key, child_value in value.items():
        keys.update(_flatten_keys(child_value, (*prefix, child_key)))
    return keys


def _leaf_values(value: object) -> list[object]:
    if not isinstance(value, dict):
        return [value]

    leaves: list[object] = []
    for child_value in value.values():
        leaves.extend(_leaf_values(child_value))
    return leaves


def test_translation_files_match_strings_keys() -> None:
    """Test that translation files match the default strings key surface."""
    expected_keys = _flatten_keys(_load_json(STRINGS_FILE))

    for translation_file in sorted(TRANSLATIONS_DIR.glob("*.json")):
        translation = _load_json(translation_file)
        assert _flatten_keys(translation) == expected_keys
        assert all(isinstance(value, str) and value for value in _leaf_values(translation)), (
            f"{translation_file.name} contains an empty or non-string translation"
        )


def test_default_translation_uses_generic_integration_name() -> None:
    """Test that the integration display name is generic."""
    strings = _load_json(STRINGS_FILE)
    english = _load_json(TRANSLATIONS_DIR / "en.json")

    assert strings["config"]["step"]["user"]["title"] == "Nice"
    assert english["config"]["step"]["user"]["title"] == "Nice"
    assert strings["config"]["step"]["user"]["data"]["host"] == "Interface IP address"


def test_catalan_translation_is_present() -> None:
    """Test representative Catalan translations."""
    catalan = _load_json(TRANSLATIONS_DIR / "ca.json")

    assert catalan["config"]["step"]["user"]["title"] == "Nice"
    assert catalan["config"]["step"]["user"]["data"]["name"] == "Nom"
    assert catalan["config"]["step"]["reconfigure"]["data_description"]["host"]
    assert catalan["config"]["error"]["unknown"] == "Error inesperat"
