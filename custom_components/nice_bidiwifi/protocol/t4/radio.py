"""Audited receiver telemetry and bounded, private radio record codecs.

JNI output/counter layouts and the Java radio checksum/scrambling consumer are
separate contracts. Nothing in this module sends a request or programs a remote.
"""

from dataclasses import dataclass, field

from ...errors import NiceProtocolError, NiceParameterError

RADIO_PROTOCOLS = {
    1: "Nice O-Code",
    2: "Nice Flor",
    3: "Nice Flo",
    4: "MHouse",
    5: "SMILO",
    6: "Peccinin",
    7: "King Gates",
    8: "V2",
    9: "ACM",
    10: "Nice ET old",
    11: "Nice ET new",
    12: "Nice BIDI PLN2+",
}


def decode_coding_type(value):
    if value is None or not 0 <= value <= 255 or value & 0x80:
        return None
    return next(
        (
            name
            for bit, name in ((0x10, "flo"), (0x40, "hcs"), (0x20, "flor"))
            if not value & bit
        ),
        None,
    )


def decode_receiver_counts(raw: bytes | None):
    if (
        not raw
        or not 2 <= len(raw) <= 200
        or (len(raw) - 2) % 3
        or raw == b"\xff" * len(raw)
    ):
        return None
    counts = []
    seen = set()
    for offset in range(2, len(raw), 3):
        code = raw[offset]
        if code in seen:
            return None
        seen.add(code)
        counts.append(
            {
                "protocol_code": code,
                "protocol": RADIO_PROTOCOLS.get(code),
                "count": int.from_bytes(raw[offset + 1 : offset + 3], "big"),
            }
        )
    return {
        "reported_total": int.from_bytes(raw[:2], "big"),
        "protocol_counts": counts,
        "sum_relationship": "not_assumed",
    }


def decode_receiver_outputs(raw: bytes | None):
    # JNI @0x15870 reads five Java shorts containing five wire bytes, not
    # nine bytes. BFI inserts element 1/3 as the high timer byte: big endian.
    if raw is None or len(raw) != 5 or raw == b"\xff" * 5:
        return None
    flags = raw[0]
    return {
        "reported_outputs": [
            {
                "channel": i + 1,
                "on": bool(flags & (1 << i)),
                "active": bool(flags & (0x10 << i)),
            }
            for i in range(4)
        ],
        "timer_1_raw": int.from_bytes(raw[1:3], "big"),
        "timer_2_raw": int.from_bytes(raw[3:5], "big"),
        "physical_relay_state": "not_inferred",
    }


@dataclass(frozen=True, slots=True)
class RadioAuthContext:
    mac: int = field(repr=False)
    certificate: int = field(repr=False)
    client_nonce: int = field(repr=False)
    receiver_nonce: int = field(repr=False)
    command: int

    def __post_init__(self):
        if (
            isinstance(self.command, bool)
            or not isinstance(self.command, int)
            or self.command not in {0x71, 0x72, 0xBE, 0xBF}
            or any(
                isinstance(x, bool)
                or not isinstance(x, int)
                or not 0 <= x <= 0xFFFFFFFF
                for x in (
                    self.mac,
                    self.certificate,
                    self.client_nonce,
                    self.receiver_nonce,
                )
            )
        ):
            raise NiceParameterError("Invalid radio authentication context")

    def signature(self, payload: bytes):
        # Manufacturer's additive check, not a modern MAC/HMAC.
        return (
            self.client_nonce
            + self.receiver_nonce
            + (self.mac ^ self.certificate)
            + sum(payload)
            + self.command
        ) & 0xFFFFFFFF


def _scramble(raw: bytes):
    # Recovered nibble transform is self-inverse. No mutable global key state.
    table = (4, 5, 3, 2, 0, 1, 7, 6, 12, 13, 11, 10, 8, 9, 15, 14)
    return bytes(table[x & 15] | table[x >> 4] << 4 for x in raw)


def encode_radio_protocols(codes, context: RadioAuthContext):
    plain = bytearray(8)
    for code in codes:
        if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code < 64:
            raise NiceParameterError("Radio protocol code is outside the bitmap")
        plain[code // 8] |= 1 << (code % 8)
    signature = context.signature(plain)
    key = (context.mac ^ context.client_nonce ^ context.receiver_nonce).to_bytes(
        4, "little"
    )
    encoded = _scramble(bytes(x ^ key[i % 4] for i, x in enumerate(plain)))
    return encoded + signature.to_bytes(4, "little")


def decode_radio_protocols(raw: bytes, context: RadioAuthContext):
    if len(raw) != 12:
        raise NiceProtocolError("Invalid signed radio bitmap length")
    key = (context.mac ^ context.client_nonce ^ context.receiver_nonce).to_bytes(
        4, "little"
    )
    plain = bytes(x ^ key[i % 4] for i, x in enumerate(_scramble(raw[:8])))
    if int.from_bytes(raw[8:], "little") != context.signature(plain):
        raise NiceProtocolError("Radio bitmap integrity check failed")
    return tuple(i for i in range(64) if plain[i // 8] & (1 << (i % 8)))


@dataclass(frozen=True, slots=True)
class RemoteMemoryRecord:
    position: int
    code: int = field(repr=False)
    rolling_counter: int = field(repr=False)
    button: int
    function: int
    t4_group: int
    priority: int
    remote_type: int
    status_nibble: int
    protocol: int | None
    model: int | None

    def public_summary(self):
        return {
            "position": self.position,
            "button": self.button,
            "function_code": self.function,
            "t4_group": self.t4_group,
            "priority": self.priority,
            "remote_type_code": self.remote_type,
            "protocol_code": self.protocol,
            "model_code": self.model,
            "empty": self.code == 0 and self.button == 0 and self.rolling_counter == 0,
            "virgin": self.code == 0x0FFFFFFF
            and self.button == 0
            and self.rolling_counter == 65535,
        }


def decode_remote_record(raw: bytes, *, position: int):
    # The Java base64 constructor consumes an eight/ten-byte record without
    # a wire position prefix. This helper is offline; it does not assume the
    # query-response framing or silently publish remote codes/counters.
    if (
        len(raw) not in {8, 10}
        or isinstance(position, bool)
        or not isinstance(position, int)
        or not 0 <= position <= 1023
    ):
        raise NiceParameterError("Invalid remote record shape or position")
    extended = len(raw) == 10 and raw[8:10] != b"\xff\xff"
    button = raw[3] >> 4
    return RemoteMemoryRecord(
        position,
        int.from_bytes(raw[:4], "little") & 0x0FFFFFFF,
        int.from_bytes(raw[4:6], "little"),
        0 if button == 15 else button,
        raw[6] >> 4,
        raw[6] & 15,
        raw[7] >> 6,
        (raw[7] >> 4) & 3,
        raw[7] & 15,
        raw[8] if extended else None,
        raw[9] if extended else None,
    )


def build_receiver_request(
    group: int, parameter: int, payload: bytes = b"", *, flags=0x99, target=(0, 10)
):
    """Separate external OXI reads from built-in controller-radio authentication."""
    from .codec import dmp_checksum

    external = {(0, 4, 0x99, 0), (10, 0x25, 0x99, 2)}
    controller = {
        (0, 4, 0x99, 0),
        (0, 7, 0x99, 0),
        (4, 0xC1, 0x89, 0),
        (4, 0xBE, 0x89, 0),
        (4, 0xBF, 0x89, 0),
        (4, 0xC1, 0xA9, 4),
        (4, 0xC1, 0x99, 0),
        (4, 0xBE, 0x99, 4),
        (4, 0xBF, 0x99, 4),
    }
    allowed = (
        external if target == (0, 10) else controller if target == (0, 3) else set()
    )
    if (group, parameter, flags, len(payload)) not in allowed:
        raise NiceParameterError("Receiver operation is outside its reviewed contract")
    body = [group, parameter, flags, 0, len(payload), *payload]
    header = [*target, 0x50, 0x91, 8, len(body) + 1]
    packet = [*header, dmp_checksum(*header), *body, dmp_checksum(*body)]
    return bytes([0x55, len(packet), *packet, len(packet)])


def decode_memory_reply(raw: bytes, *, expected_position: int):
    if len(raw) not in {10, 12}:
        raise NiceProtocolError(
            "Incomplete receiver memory record", code="memory_record_length"
        )
    position = int.from_bytes(raw[:2], "big")
    if position != expected_position:
        raise NiceProtocolError(
            "Receiver memory position echo differs from request",
            code="memory_position_echo",
        )
    return decode_remote_record(raw[2:], position=position)
