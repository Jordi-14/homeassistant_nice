"""Source-backed enrollment and paired-user XML. No network or implicit mutation."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
import xml.etree.ElementTree as ET

from ...errors import NiceParameterError, NicePermissionError, NiceProtocolError
from .codec import xml_escape, xml_payload

MAX_USERS = 64
MAX_ACCESS_XML = 65536


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"
    DISABLED = "disable"
    WAIT = "wait"
    UNKNOWN = "unknown"


def bounded_identifier(value: str, *, limit=128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise NiceParameterError("Invalid access identity")
    return value


def setup_check(setup_code: str, challenge: str) -> str:
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", challenge):
        raise NiceParameterError("Invalid enrollment challenge")
    if (
        not isinstance(setup_code, str)
        or not 1 <= len(setup_code) <= 64
        or any(ord(c) < 32 for c in setup_code)
    ):
        raise NiceParameterError("Invalid module setup code")
    data = setup_code.encode() + bytes.fromhex(challenge)[::-1] + b"Nice4U"
    return f"{(~binascii.crc32(data)) & 0xFFFFFFFF:08X}"


def first_pair_password(
    setup_code: str, server_challenge: str, client_challenge: str
) -> bytes:
    setup_check(setup_code, client_challenge)
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", server_challenge):
        raise NiceProtocolError("Invalid server enrollment challenge")
    return hashlib.sha256(
        setup_code.encode()
        + bytes.fromhex(server_challenge)[::-1]
        + bytes.fromhex(client_challenge)[::-1]
        + b"Nice"
    ).digest()


def parse_response(frame: bytes, kind: str) -> ET.Element:
    if len(frame) > MAX_ACCESS_XML:
        raise NiceProtocolError("Access response exceeds retention limit")
    try:
        root = ET.fromstring(xml_payload(frame))
    except (ET.ParseError, UnicodeError, ValueError) as err:
        raise NiceProtocolError("Malformed access response") from err
    if root.tag != "Response" or root.get("type") != kind:
        raise NiceProtocolError("Unexpected access response type")
    error = root.find("./Error/Code")
    if error is not None:
        from ...errors import NiceResponseError, ProtocolLayer

        code = (error.text or "").strip()
        if not re.fullmatch(r"\d{1,3}", code):
            raise NiceProtocolError("Invalid access error code")
        raise NiceResponseError(
            "Access request rejected: " + code,
            layer=ProtocolLayer.NHK,
            code=code,
            request_type=kind,
        )
    return root


@dataclass(frozen=True, slots=True, repr=False)
class PairedUser:
    id: str
    username: str
    controller_id: str
    role: UserRole
    description: str = ""
    pending: bool = False

    @property
    def identity(self):
        return self.username, self.controller_id


@dataclass(frozen=True, slots=True, repr=False)
class PairingsSnapshot:
    users: tuple[PairedUser, ...]
    complete: bool = True

    def principal(self, username, controller_id):
        matches = [
            user
            for user in self.users
            if user.identity == (username, controller_id) and not user.pending
        ]
        return matches[0] if len(matches) == 1 else None

    def summary(self):
        return {
            "complete": self.complete,
            "users": sum(not u.pending for u in self.users),
            "pending": sum(u.pending for u in self.users),
            "roles": {
                str(role): sum(u.role is role and not u.pending for u in self.users)
                for role in UserRole
            },
        }


def parse_pairings(frame: bytes) -> PairingsSnapshot:
    root = parse_response(frame, "PAIRINGS")
    users = []
    seen = set()
    # Both lists are optional in the app schema; an absent PreAss is empty.
    # Without the Users container there is no complete administration snapshot.
    complete = root.find("Users") is not None and root.get(
        "more", "false"
    ).lower() not in {"true", "1"}
    for container, pending in [("Users", False), ("PreAss", True)]:
        for node in root.findall(f"./{container}/User"):
            if len(users) >= MAX_USERS:
                raise NiceProtocolError("Pairing list exceeds safe limit")
            identity = (
                bounded_identifier(node.get("username", "")),
                bounded_identifier(node.get("controllerID", "")),
            )
            if identity in seen:
                raise NiceProtocolError("Duplicate pairing identity")
            seen.add(identity)
            raw_role = node.get("perm", "wait" if pending else "unknown")
            role = (
                UserRole(raw_role)
                if raw_role in UserRole._value2member_map_
                else UserRole.UNKNOWN
            )
            user = PairedUser(
                bounded_identifier(node.get("id", ""), limit=32),
                *identity,
                role,
                bounded_identifier(node.get("desc") or " ", limit=96).strip(),
                pending,
            )
            users.append(user)
    return PairingsSnapshot(tuple(users), complete=complete)


def validate_user_change(
    snapshot: PairingsSnapshot,
    principal: tuple[str, str],
    target: PairedUser,
    operation: str,
    role: UserRole,
):
    if not snapshot.complete:
        raise NicePermissionError("Complete user list is required")
    actor = snapshot.principal(*principal)
    if actor is None or actor.role is not UserRole.ADMIN:
        raise NicePermissionError(
            "Verified current administrator permission is required"
        )
    matches = [user for user in snapshot.users if user.identity == target.identity]
    if len(matches) != 1 or matches[0] != target:
        raise NicePermissionError("Target user changed; refresh the list")
    if role not in {UserRole.ADMIN, UserRole.USER, UserRole.DISABLED, UserRole.WAIT}:
        raise NiceParameterError("Unknown target permission")
    if target.role is UserRole.UNKNOWN:
        raise NicePermissionError("Target permission is not understood")
    if operation == "approve":
        if not target.pending or role is not UserRole.USER:
            raise NicePermissionError("Only pending users can be approved as users")
    elif operation == "edit":
        if target.role is role:
            raise NicePermissionError("Permission is already set; no change is needed")
        if target.pending:
            raise NicePermissionError(
                "Approve pending users before editing permissions"
            )
        if target.identity == principal:
            raise NicePermissionError(
                "Current integration access cannot be changed by this workflow"
            )
        if (
            target.role is UserRole.ADMIN
            and role is not UserRole.ADMIN
            and sum(u.role is UserRole.ADMIN and not u.pending for u in snapshot.users)
            <= 1
        ):
            raise NicePermissionError("Cannot remove the sole administrator")
    else:
        raise NiceParameterError("Unresolved ownership/removal operation")


def user_change_body(target: PairedUser, role: UserRole) -> str:
    if role is UserRole.UNKNOWN:
        raise NiceParameterError("Unknown target permission")
    attributes = {
        "id": target.id,
        "username": target.username,
        "controllerID": target.controller_id,
        "perm": role.value,
        "CType": "unknown",
        "OSVer": "",
        "desc": target.description,
    }
    return (
        "<User "
        + " ".join(f'{key}="{xml_escape(value)}"' for key, value in attributes.items())
        + "/>\r\n"
    )


@dataclass(frozen=True, slots=True, repr=False)
class EnrollmentReply:
    username: str
    controller_id: str
    role: UserRole
    password: bytes | None


def parse_enrollment(
    frame: bytes, kind: str, username: str, controller_id: str
) -> EnrollmentReply:
    root = parse_response(frame, kind)
    node = root.find("./Authentication")
    if node is None:
        raise NiceProtocolError("No enrollment authentication record")
    # Missing identity echoes do not authorize adopting a different principal.
    if node.get("username") not in {None, username} or node.get("controllerID") not in {
        None,
        controller_id,
    }:
        raise NiceProtocolError("Enrollment identity does not match the request")
    raw_role = node.get("perm", "unknown")
    if not node.get("pwd"):
        return EnrollmentReply(
            username,
            controller_id,
            UserRole(raw_role)
            if raw_role in UserRole._value2member_map_
            else UserRole.UNKNOWN,
            None,
        )
    try:
        password = base64.b64decode(node.get("pwd", ""), validate=True)
    except (ValueError, binascii.Error) as err:
        raise NiceProtocolError("Malformed enrollment password") from err
    if len(password) != 32:
        raise NiceProtocolError("Enrollment did not return a complete password")
    raw_role = node.get("perm", "unknown")
    role = (
        UserRole(raw_role)
        if raw_role in UserRole._value2member_map_
        else UserRole.UNKNOWN
    )
    return EnrollmentReply(username, controller_id, role, password)
