"""Pure NHK framing and signing helpers."""

from __future__ import annotations

import hashlib
import re

STX = b"\x02"
ETX = b"\x03"


def sha256(*values: bytes) -> bytes:
    """Return a SHA-256 digest over the supplied byte sequences."""
    digest = hashlib.sha256()
    for value in values:
        digest.update(value)
    return digest.digest()


def reverse_hex(value: str) -> bytes:
    """Decode a hexadecimal string and reverse its byte order."""
    return bytes.fromhex(value)[::-1]


def printable(data: bytes) -> str:
    """Return a log-safe printable representation of an NHK frame."""
    return (
        data.decode("utf-8", errors="replace")
        .replace("\x02", "<STX>")
        .replace("\x03", "<ETX>")
    )


def xml_attribute(text: str, name: str) -> str | None:
    """Return one XML attribute from a small trusted protocol fragment."""
    match = re.search(rf'\b{name}="([^"]*)"', text)
    return match.group(1) if match else None


def xml_escape(value: str) -> str:
    """Escape a value for an NHK XML attribute or text field."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def frame_xml(xml: str) -> bytes:
    """Wrap XML in NHK STX/ETX framing."""
    return STX + xml.encode("utf-8") + ETX


def xml_payload(frame: bytes) -> str:
    """Decode XML from an optionally framed NHK payload."""
    payload = frame
    if payload.startswith(STX):
        payload = payload[1:]
    if payload.endswith(ETX):
        payload = payload[:-1]
    return payload.decode("utf-8", errors="replace")


def response_matches(
    response: bytes,
    expected_type: str | None,
    expected_id: int | None,
) -> bool:
    """Return whether an NHK frame matches an outstanding request."""
    text = printable(response)
    if "<Response " not in text:
        return False
    if expected_type and xml_attribute(text, "type") != expected_type:
        return False
    if expected_id is not None and xml_attribute(text, "id") != str(expected_id):
        return False
    return True


def response_summary(response: bytes) -> str:
    """Return a credential-safe summary of an NHK response."""
    text = printable(response)
    response_type = xml_attribute(text, "type") or "unknown"
    response_id = xml_attribute(text, "id") or "unknown"
    error = " error=yes" if re.search(r"<Error\b", text) else ""
    t4_payload_count = len(re.findall(r"<T4\b", text))
    return (
        f"type={response_type} id={response_id} bytes={len(response)} "
        f"t4_payloads={t4_payload_count}{error}"
    )


def response_error_summary(response: bytes) -> str:
    """Return a safe error summary while retaining a machine-readable code."""
    text = printable(response)
    match = re.search(r"<Code>\s*([A-Za-z0-9_.-]{1,32})\s*</Code>", text)
    if match:
        error = f"<Error><Code>{match.group(1)}</Code></Error>"
    else:
        message_match = re.search(
            r"<Error>\s*([A-Za-z0-9_. -]{1,64})\s*</Error>",
            text,
        )
        error = (
            f"protocol error: {message_match.group(1).strip()}"
            if message_match
            else "protocol error"
        )
    return f"{error} ({response_summary(response)})"
