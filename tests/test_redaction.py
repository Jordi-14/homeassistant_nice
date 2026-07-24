"""Tests for centralized bounded redaction."""

from __future__ import annotations

from custom_components.nice_bidiwifi.protocol.nhk.codec import (
    frame_xml,
    response_error_summary,
)
from custom_components.nice_bidiwifi.redaction import (
    allowed_config_diagnostics,
    bounded_protocol_observations,
    configured_secrets,
    redact_text,
)


def test_free_text_redaction_is_case_insensitive_and_complete() -> None:
    """Configured identifiers cannot leak through error text."""
    config = {
        "host": "gate.local",
        "username": "Owner",
        "password": "ABCD",
        "target_mac": "AA:BB:CC:DD:EE:FF",
    }

    redacted = redact_text(
        "OWNER failed at gate.local with abcd for aa:bb:cc:dd:ee:ff",
        configured_secrets(config),
    )

    assert redacted == (
        "<redacted> failed at <redacted> with <redacted> for <redacted>"
    )


def test_diagnostic_config_and_protocol_data_are_allowlisted_and_bounded() -> None:
    """Diagnostics omit unknown config fields and malformed protocol keys."""
    config = {
        "name": "Gate",
        "host": "gate.local",
        "password": "secret",
        "unexpected_token": "must-not-appear",
    }
    observations = bounded_protocol_observations(
        {
            "04/01": "02",
            "<bad-key>": "hidden",
            "NHK/Unknown": "x" * 300,
        },
        value_limit=8,
    )

    assert "unexpected_token" not in allowed_config_diagnostics(config)
    assert observations == {
        "04/01": "02",
        "NHK/Unknown": "xxxxxxxx",
    }


def test_protocol_error_summary_retains_code_without_identifiers() -> None:
    """Protocol errors retain routing-safe codes without raw frame contents."""
    summary = response_error_summary(
        frame_xml(
            '<Response type="T4_REQUEST" id="3" '
            'source="AA:BB:CC:DD:EE:FF">'
            "<Error><Code>14</Code></Error></Response>"
        )
    )

    assert "<Code>14</Code>" in summary
    assert "AA:BB:CC:DD:EE:FF" not in summary
