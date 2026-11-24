"""Model-specific evidence limits; identity matches do not invent support."""

from __future__ import annotations

from .models.profiles import normalized_device_identity


def diagnostic_profile(info):
    identity = normalized_device_identity(info)
    if "mc824h" in identity or "hyke" in identity:
        key = "mc824h_hyke"
        history = "All-zero 04/DA was reported alongside module-log interventions; slot freshness and validity remain unverified."
        gaps = (
            "mc824h.advanced_history_layout",
            "mc824h.motor_parameter_layout",
            "diagnostics.advanced_history",
        )
    elif "robus" in identity and info is not None and info.device_fw_version == "FG01h":
        key = "robus_fg01h"
        history = "Static all-zero history was observed on this family; use event evidence rather than assume eight fresh successes."
        gaps = ("fg01h.force_map_learning", "diagnostics.advanced_history")
    else:
        key = "conservative_unknown"
        history = "No model-specific maneuver-slot validity or newest-slot convention is established."
        gaps = ("diagnostics.advanced_history",)
    return {
        "key": key,
        "identity_basis": "reported_INFO_strings",
        "maneuver_history_policy": history,
        "success_validity": "not_established",
        "newest_slot": "unknown",
        "hardware_scaling": "controller_INFO_availability_and_scales",
        "motor_identity": "only_decoded_descriptor_fields_no_inferred_aggregate",
        "legacy_abnormal_stop_detector": "inferred_experimental_heuristic_not_physical_cause",
        "gaps": gaps,
    }
