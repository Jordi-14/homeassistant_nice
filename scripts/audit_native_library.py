"""Fingerprint the privately supplied artifact and verify audited symbol anchors.

This tool reads ELF bytes only. It does not load or execute the Android library.
The vendor binary is not part of the repository or runtime dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "custom_components/nice_bidiwifi/native_decoder_evidence.json"


def audit(path: Path, pro_split: Path | None = None) -> dict:
    evidence = json.loads(EVIDENCE.read_text())
    if path.stat().st_size != evidence["artifact"]["bytes"]:
        raise ValueError("Unexpected native artifact size")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != evidence["artifact"]["sha256"]:
        raise ValueError("Native artifact fingerprint differs from the reviewed library")
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected little-endian ELF64")
    offset = struct.unpack_from("<Q", data, 40)[0]
    size, count = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(count)]
    symbols = {}
    for section in sections:
        if section[1] != 11:
            continue
        strings = sections[section[6]]
        table = data[strings[4]:strings[4] + strings[5]]
        for index in range(section[4], section[4] + section[5], section[9]):
            name, _, _, _, value, length = struct.unpack_from("<IBBHQQ", data, index)
            text = table[name:table.find(b"\0", name)].decode()
            if value:
                symbols[text] = (value, length)
    for contract in evidence["contracts"]:
        expected = (int(contract["address"], 16), contract["symbol_bytes"])
        if symbols.get(contract["symbol"]) != expected:
            raise ValueError("Native symbol anchor differs: " + contract["key"])
    matched = None
    if pro_split is not None:
        with zipfile.ZipFile(pro_split) as archive:
            info = archive.getinfo("lib/arm64-v8a/libnative-lib.so")
            if info.file_size != len(data):
                raise ValueError("Pro split contains a different library")
            matched = archive.read(info) == data
            if not matched:
                raise ValueError("Pro split contains different library bytes")
    return {
        "sha256": evidence["artifact"]["sha256"],
        "audited_symbol_anchors": len(evidence["contracts"]),
        "pro_split_library_matches": matched,
        "validation": "fingerprint_and_symbols_not_dynamic_behavior",
        "vendor_code_executed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--pro-split", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.library, args.pro_split)))


if __name__ == "__main__":
    main()
