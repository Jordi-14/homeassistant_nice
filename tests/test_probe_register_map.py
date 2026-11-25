"""The register-map probe sends read requests only and skips procedures and secrets."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import probe_register_map as probe  # noqa: E402


class FakeClient:
    """Answer INFO with FD for odd 04 parameters (except the secrets) and OK otherwise; record every request."""

    t4_timeout_ms = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sent: list[tuple[tuple[int, int], int, int, int]] = []

    def _ensure_connected_locked(self) -> None:
        pass

    def _t4_request_locked(self, protocol, payload, daddr, dendpoint, tout_ms):
        assert protocol == "DMP"
        target, group, parameter, flags = (payload[2], payload[3]), payload[9], payload[10], payload[11]
        self.sent.append((target, group, parameter, flags))
        status = 0xFD if group == 0x04 and parameter % 2 and parameter not in probe.SECRET_04 else 0x00
        op = 0x09 if flags == probe.INFO else 0x19
        data = b"" if status else b"\x01"
        from custom_components.nice_bidiwifi.protocol.t4.codec import dmp_checksum
        header = [0x50, 0x91, *target, 8, len(data) + 6]
        payload = [group, parameter, op, 42, status, *data]
        body = [*header, dmp_checksum(*header), *payload, dmp_checksum(*payload)]
        return b"<Response />", [bytes([0x55, len(body), *body, len(body)])]


def test_probe_reads_only_and_skips_procedures_and_secrets() -> None:
    client = FakeClient()

    rows = probe.probe(client, include_oxi=True)

    assert {flags for *_, flags in client.sent} == {probe.INFO, probe.GET}
    gets = {(t, g, p) for t, g, p, f in client.sent if f == probe.GET}
    # no GET of a procedure register, nor of a parameter the controller does not support
    assert not any((probe.CONTROLLER, 0x04, p) in gets for p in probe.NO_GET_04)
    assert not any((probe.CONTROLLER, 0x04, p) in gets for p in range(1, 0x100, 2))
    # a controller that supports the password / radio-code registers: INFO is read, the value never
    assert all(
        (probe.CONTROLLER, 0x04, p, probe.INFO) in client.sent for p in probe.SECRET_04
    )
    assert not any((probe.CONTROLLER, 0x04, p) in gets for p in probe.SECRET_04)
    fb = next(r for r in rows if r["register"] == "04/FB")
    assert fb["info"]["status"] == "OK" and fb["get"] == {"status": "not sent", "data": ""}
    assert (probe.CONTROLLER, 0x04, 0x80) in gets
    # OXI: never PIN (0A/10), keys (0A/31-35), remotes (0A/25) or a STD MAC / serial
    oxi = {(g, p) for t, g, p, _ in client.sent if t == probe.OXI}
    assert oxi.isdisjoint({(0x0A, 0x10), (0x0A, 0x25), (0x0A, 0x31), (0x0A, 0x35), (0x00, 0x07), (0x00, 0x20)})
    assert len([r for r in rows if r["register"].startswith("04/")]) == 256
    assert rows[0]["info"] == {"status": "OK", "data": "01"}


def test_write_frames_are_refused() -> None:
    from custom_components.nice_bidiwifi.protocol.t4.dmp import build_dmp_write_frame

    assert probe.is_read_frame(probe.build_dmp_read_frame(0, 3, 4, 0x81, flags=probe.GET))
    assert probe.is_read_frame(probe.build_dmp_read_frame(0, 3, 4, 0x81, flags=probe.INFO))
    assert not probe.is_read_frame(build_dmp_write_frame(0, 3, 4, 0x81, b"\x1e"))
    assert not probe.is_read_frame(probe.build_dmp_read_frame(0, 3, 4, 0x81, flags=0xA9))


def test_corrupt_info_does_not_trigger_a_value_probe():
    client = FakeClient()
    original = client._t4_request_locked
    def corrupt(*args):
        response, plains = original(*args)
        raw = bytearray(plains[0])
        raw[-2] ^= 1
        return response, [bytes(raw)]
    client._t4_request_locked = corrupt
    rows = probe.probe(client, include_oxi=False)
    assert not any(flags == probe.GET for *_, flags in client.sent)
    assert all(row['info']['status'] == 'malformed reply' for row in rows)
