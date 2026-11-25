"""Bounded STD_PROFILE broadcast decoding, without permissions or absence claims."""

from ...errors import NiceProtocolError, NiceResponseError
from .replies import checked_dmp_reply

PROFILE_NAMES = {
    0: "standard",
    1: "oview",
    2: "pc",
    4: "controller",
    6: "screen",
    7: "gateway",
    10: "receiver",
    17: "power_supply",
}
MAX_PROFILE_REPLIES = 64


def decode_profile_replies(plains):
    if len(plains) > MAX_PROFILE_REPLIES:
        raise NiceProtocolError("Bus profile reply batch exceeds its bound")
    devices = {}
    rejected = []
    ignored = 0
    for plain in plains:
        if len(plain) < 16 or len(plain) > 258 or plain[0] != 0x55:
            ignored += 1
            continue
        target = tuple(plain[4:6])
        if 255 in target or target == (0, 0):
            ignored += 1
            continue
        try:
            reply = checked_dmp_reply(
                plain, target=target, group=0, parameter=4, operation=0x19
            )
        except NiceResponseError as error:
            rejected.append(
                {"source": f"{target[0]:02X}.{target[1]:02X}", "code": error.code}
            )
            continue
        if reply is None:
            ignored += 1
            continue
        if not reply.value:
            raise NiceProtocolError("Bus profile has an unverified layout")
        # Pro reads firstOrNull; extra reported bytes are not interpreted.
        profile = reply.value[0]
        previous = devices.get(target)
        if previous is not None and previous != profile:
            raise NiceProtocolError("Bus source reported conflicting profiles")
        devices[target] = profile
    return devices, rejected, ignored
