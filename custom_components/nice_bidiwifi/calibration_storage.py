"""Calibration profile validation and migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .calibration_types import CalibrationProfile

PROFILE_VERSION_BY_MODE = {
    "encoder": 5,
    "time": 6,
    "live_percent": 7,
    "live_scalar": 7,
}


def migrate_calibration_profile(
    profile: dict[str, Any],
) -> tuple[CalibrationProfile, bool]:
    """Validate and migrate one stored calibration profile."""
    mode = str(profile.get("mode") or "encoder")
    target_version = PROFILE_VERSION_BY_MODE.get(mode)
    if target_version is None:
        raise ValueError(f"Unsupported calibration profile mode: {mode}")

    version = profile.get("version")
    if not isinstance(version, int) or version < 1:
        version = 1
    if version > target_version:
        raise ValueError(
            f"Calibration profile version {version} is newer than supported "
            f"version {target_version} for mode {mode}"
        )

    required_defaults: dict[str, Any] = {
        "mode": mode,
        "targets": [] if mode == "time" else [20, 40, 60, 80],
        "bounds": {},
        "travel_speed": {},
        "samples": {"open": [], "close": []},
        "events": [],
    }
    needs_migration = version != target_version or any(
        key not in profile for key in required_defaults
    )
    if not needs_migration:
        return profile, False

    migrated = deepcopy(profile)
    for key, default in required_defaults.items():
        migrated.setdefault(key, default)
    migrated["version"] = target_version
    return migrated, True
