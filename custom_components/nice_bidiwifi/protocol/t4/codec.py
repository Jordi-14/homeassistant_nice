"""Pure T4 encoding and decoding helpers."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import string

from ..nhk.codec import printable, xml_attribute

T4_RE = re.compile(r"<T4\b(?P<attrs>[^>]*)>(?P<body>.*?)</T4>", re.DOTALL)


def xor_sha256(data: bytes, key: bytes) -> bytes:
    """Apply the Nice T4 SHA-256 XOR stream."""
    digest = hashlib.sha256(key).digest()
    return bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(data))


def random_t4_key(length: int = 31) -> bytes:
    """Return a random ASCII key for one T4 exchange."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length)).encode("ascii")


def dmp_checksum(*values: int) -> int:
    """Return the XOR checksum used by DMP frames."""
    checksum = 0
    for value in values:
        checksum ^= value
    return checksum & 0xFF


def decrypt_t4_payloads_from_frame(frame: bytes) -> list[bytes]:
    """Return decrypted T4 payloads from one NHK XML frame."""
    text = printable(frame)
    payloads: list[bytes] = []
    for match in T4_RE.finditer(text):
        key_value = xml_attribute(match.group("attrs"), "key")
        if not key_value:
            continue
        try:
            key = base64.b64decode(key_value)
            encrypted = base64.b64decode(match.group("body").strip())
        except ValueError:
            continue
        payloads.append(xor_sha256(encrypted, key))
    return payloads
