"""The register-map probe sends read requests only and skips procedures and secrets."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import probe_register_map as probe  # noqa: E402


class FakeClient:
    """Answer INFO with FD for odd 04 parameters and OK otherwise; record every request."""

    t4_timeout_ms = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sent: list[tuple[tuple[int, int], int, int, int]] = []

    def _ensure_connected_locked(self) -> None:
        pass

    def _t4_request_locked(self, protocol, payload, daddr, dendpoint, tout_ms):
        target, group, parameter, flags = payload
        self.sent.append((target, group, parameter, flags))
        status = 0xFD if group == 0x04 and parameter % 2 else 0x00
        op = 0x09 if flags == probe.INFO else 0x19
        data = b"" if status else b"\x01"
        frame = bytes(9) + bytes([group, parameter, op, len(data), status]) + data + b"\x00"
        return b"<Response />", [b"\x55" + frame[1:]]


def test_probe_reads_only_and_skips_procedures_and_secrets(monkeypatch) -> None:
    monkeypatch.setattr(
        probe, "build_dmp_read_frame", lambda daddr, dep, g, p, *, flags: ((daddr, dep), g, p, flags)
    )
    client = FakeClient()

    rows = probe.probe(client, include_oxi=True)

    assert {flags for *_, flags in client.sent} == {probe.INFO, probe.GET}
    gets = {(t, g, p) for t, g, p, f in client.sent if f == probe.GET}
    # no GET of a procedure register, nor of a parameter the controller does not support
    assert not any((probe.CONTROLLER, 0x04, p) in gets for p in probe.NO_GET_04)
    assert not any((probe.CONTROLLER, 0x04, p) in gets for p in range(1, 0x100, 2))
    assert (probe.CONTROLLER, 0x04, 0x80) in gets
    # OXI: never PIN (0A/10), keys (0A/31-35), remotes (0A/25) or a STD MAC / serial
    oxi = {(g, p) for t, g, p, _ in client.sent if t == probe.OXI}
    assert oxi.isdisjoint({(0x0A, 0x10), (0x0A, 0x25), (0x0A, 0x31), (0x0A, 0x35), (0x00, 0x07), (0x00, 0x20)})
    assert len([r for r in rows if r["register"].startswith("04/")]) == 256
    assert rows[0]["info"] == {"status": "OK", "data": "01"}
