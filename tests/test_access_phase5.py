"""Enrollment identity durability, role boundaries and source-backed setup codecs."""

import asyncio
import base64
import xml.etree.ElementTree as ET
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi import config_flow
from custom_components.nice_bidiwifi.enrollment import (
    EnrollmentClient,
    EnrollmentIdentity,
)
from custom_components.nice_bidiwifi.errors import (
    NiceParameterError,
    NicePermissionError,
    NiceProtocolError,
    NiceUnsupportedError,
)
from custom_components.nice_bidiwifi.pairing_intents import PairingIntents
from custom_components.nice_bidiwifi.protocol.nhk.access import (
    PairedUser,
    PairingsSnapshot,
    UserRole,
    first_pair_password,
    parse_enrollment,
    parse_pairings,
    setup_check,
    user_change_body,
    validate_user_change,
)
from custom_components.nice_bidiwifi.protocol.nhk.provisioning import (
    ProvisioningMutationAdapter,
    WifiConfiguration,
    parse_wnc_info,
    parse_wnc_scan,
)
from custom_components.nice_bidiwifi.session_state import (
    SessionRecoveryDeferred,
    SessionState,
)
from tests.conftest import FakeClient, make_status
from tests.test_coordinator import _coordinator

TARGET = "AA:BB:CC:DD:EE:FF"
ADMIN = PairedUser("1", "user", "source", UserRole.ADMIN)
OTHER = PairedUser("2", "other", "phone", UserRole.USER, "A & B")
PENDING = PairedUser("3", "pending", "phone2", UserRole.WAIT, pending=True)


def xml(kind, body="", **attrs):
    return (
        ET.tostring(ET.Element("Response", type=kind, **attrs))
        if not body
        else f'<Response type="{kind}">{body}</Response>'.encode()
    )


def test_setup_check_source_vectors():
    # Independently calculated complemented CRC32 and SHA256 vectors, preserving hyphens.
    assert setup_check("123-456-789", "01020304") == "2914E1E5"
    assert (
        first_pair_password("123-456-789", "AABBCCDD", "01020304").hex()
        == "1941802ba9b6a0ad95cb88b436134ad1a10282d0642cf5f4e13c6926624f7f79"
    )
    with pytest.raises(NiceParameterError):
        setup_check("secret", "12")


def test_pairing_parser_drops_secrets_and_accepts_absent_pending_container():
    snapshot = parse_pairings(
        xml(
            "PAIRINGS",
            '<Users><User id="1" username="user" controllerID="source" perm="admin" pwd="secret" desc="HA"/></Users>',
        )
    )
    assert snapshot.complete
    assert snapshot.principal("user", "source").role is UserRole.ADMIN
    assert snapshot.principal("user", "different") is None
    assert "secret" not in repr(snapshot)
    assert "user" not in repr(snapshot)
    assert snapshot.summary()["roles"]["admin"] == 1
    assert not parse_pairings(xml("PAIRINGS")).complete


def test_pairing_parser_rejects_duplicates_and_excess():
    row = '<User id="1" username="user" controllerID="source" perm="admin"/>'
    with pytest.raises(NiceProtocolError, match="Duplicate"):
        parse_pairings(xml("PAIRINGS", "<Users>" + row * 2 + "</Users>"))
    rows = "".join(
        f'<User id="{i}" username="u{i}" controllerID="s"/>' for i in range(65)
    )
    with pytest.raises(NiceProtocolError, match="limit"):
        parse_pairings(xml("PAIRINGS", "<Users>" + rows + "</Users>"))


@pytest.mark.parametrize(
    "case",
    [
        "incomplete",
        "not_admin",
        "changed",
        "self",
        "unknown",
        "pending_edit",
        "ownership",
    ],
)
def test_user_changes_fail_closed(case):
    actor, target, operation, role = ADMIN, OTHER, "edit", UserRole.ADMIN
    complete = True
    if case == "incomplete":
        complete = False
    if case == "not_admin":
        actor = replace(ADMIN, role=UserRole.USER)
    if case == "self":
        target = actor
    if case == "unknown":
        target = replace(OTHER, role=UserRole.UNKNOWN)
    if case == "pending_edit":
        target = PENDING
    if case == "ownership":
        operation = "remove"
    rows = (actor, target) if target != actor else (actor,)
    snapshot = PairingsSnapshot(rows, complete)
    if case == "changed":
        target = replace(target, description="new")
    with pytest.raises((NiceParameterError, NicePermissionError)):
        validate_user_change(snapshot, ADMIN.identity, target, operation, role)


def test_pending_approval_and_xml_escaping():
    snapshot = PairingsSnapshot((ADMIN, OTHER, PENDING))
    validate_user_change(snapshot, ADMIN.identity, PENDING, "approve", UserRole.USER)
    validate_user_change(snapshot, ADMIN.identity, OTHER, "edit", UserRole.ADMIN)
    with pytest.raises(NicePermissionError):
        validate_user_change(
            snapshot, ADMIN.identity, PENDING, "approve", UserRole.ADMIN
        )
    node = ET.fromstring(user_change_body(OTHER, UserRole.ADMIN))
    assert node.get("desc") == "A & B"
    assert node.get("controllerID") == OTHER.controller_id


def test_enrollment_identity_password_and_unknown_roles():
    password = base64.b64encode(b"x" * 32).decode()
    reply = parse_enrollment(
        xml(
            "PAIR",
            f'<Authentication username="u" controllerID="s" perm="admin" pwd="{password}"/>',
        ),
        "PAIR",
        "u",
        "s",
    )
    assert reply.password == b"x" * 32
    assert password not in repr(reply)
    assert (
        parse_enrollment(
            xml("VERIFY", '<Authentication perm="future"/>'), "VERIFY", "u", "s"
        ).role
        is UserRole.UNKNOWN
    )
    with pytest.raises(NiceProtocolError, match="identity"):
        parse_enrollment(
            xml("PAIR", '<Authentication username="other"/>'), "PAIR", "u", "s"
        )
    with pytest.raises(NiceProtocolError, match="password"):
        parse_enrollment(xml("PAIR", '<Authentication pwd="bad"/>'), "PAIR", "u", "s")


async def test_enrollment_intents_survive_new_instances_and_claim_once(hass):
    first, second = PairingIntents(hass), PairingIntents(hass)
    identity, state, kind = await first.get(TARGET, "first_user")
    assert state == "created" and kind == "first_user"
    results = await asyncio.gather(first.claim(identity), second.claim(identity))
    assert sorted(results) == [False, True]
    restored, state, kind = await second.get(TARGET, "request_access")
    assert restored == identity and state == "dispatched" and kind == "first_user"
    await second.set_state(TARGET, "rejected")
    restored, state, kind = await first.get(TARGET, "request_access")
    assert restored == identity and kind == "request_access"
    assert await first.claim(restored)


async def test_unconfirmed_user_change_has_no_io(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.access.async_refresh = AsyncMock()
    with pytest.raises(HomeAssistantError, match="confirmation"):
        await owner.access.async_change(OTHER, "edit", UserRole.ADMIN)
    owner.access.async_refresh.assert_not_awaited()


async def test_admin_scope_freshness_and_moving_gate(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.data = make_status(state="closed")
    await owner.access.async_prepare_admin()
    owner.access.require_admin()
    owner.client.reconnect_count += 1
    assert owner.access.role is UserRole.UNKNOWN
    with pytest.raises(NicePermissionError):
        owner.access.require_admin()
    owner.data = make_status(state="opening")
    with pytest.raises(NicePermissionError, match="stationary"):
        owner.access.stationary_guard()


def test_session_backoff_and_code1_policy(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.session_state.time.monotonic", lambda: now[0]
    )
    state = SessionState()
    for attempt, delay in [(1, 2), (2, 4), (3, 294)]:
        state.before_connect()
        state.rejected("15")
        assert state.attempts == attempt
        with pytest.raises(SessionRecoveryDeferred):
            state.before_connect()
        now[0] += delay
    state.before_connect()
    assert state.attempts == 0
    state.rejected("1")
    state.closed()
    assert state.state == "pairing_identity_rejected"
    assert "unresolved" in state.diagnostics()["code1_policy"]


def test_wnc_namespace_private_scan_and_configuration_boundary():
    info = parse_wnc_info(
        b'<Response type="INFO" protocolType="WNC" source="AA:BB:CC:DD:EE:FF"><Interface><Type>BiDi</Type><VersionFW>1.0</VersionFW><key_data>secret</key_data></Interface></Response>',
        TARGET,
    )
    assert info.source_matches and not info.ap_state_verified
    assert "secret" not in repr(info)
    networks = parse_wnc_scan(
        xml(
            "SCAN",
            '<WLAN SSID="Private WiFi" Security="WPA2" Channel="6" RSSI="-42" BSSID="private"/>',
        )
    )
    assert networks[0].channel == 6 and networks[0].signal == -42
    assert "Private WiFi" not in repr(networks)
    with pytest.raises(NiceProtocolError, match="namespace"):
        parse_wnc_scan(b'<Response type="SCAN" protocolType="NHK"/>')
    request = WifiConfiguration("A & B", "password123", "ES")
    node = ET.fromstring(request.body())
    assert node.findtext("./CONF/Mode") == "NHK"
    assert base64.b64decode(node.findtext("./WLAN/SSIDb64")).decode() == "A & B"
    assert node.findtext("./CONF/CloudUrl") is None
    with pytest.raises(NiceUnsupportedError, match="recovery"):
        ProvisioningMutationAdapter().configure(request)


async def test_interrupted_pairing_resumes_verify_without_pair_replay(
    hass, monkeypatch
):
    flow = config_flow.NiceBidiConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._enrollment_exchange = AsyncMock(side_effect=OSError("connection lost"))
    data = {
        "host": "192.0.2.1",
        "port": 443,
        "name": "Gate",
        "target_mac": TARGET,
        "enrollment_kind": "request_access",
        "confirm_enrollment": True,
    }
    result = await flow.async_step_pairing(data)
    assert result["step_id"] == "pairing_wait"
    assert flow._enrollment_exchange.await_count == 1
    other = config_flow.NiceBidiConfigFlow()
    other.hass = hass
    other.context = {"source": "user"}
    other._enrollment_exchange = AsyncMock()
    result = await other.async_step_pairing(data)
    assert result["step_id"] == "pairing_wait"
    other._enrollment_exchange.assert_not_awaited()
    assert other._pair_identity == flow._pair_identity


@pytest.mark.parametrize("code", ["3", "4"])
def test_session_read_recovery_is_bounded_and_write_is_not_replayed(monkeypatch, code):
    from types import SimpleNamespace

    from custom_components.nice_bidiwifi.errors import (
        NiceBidiAuthError,
        NiceResponseError,
        ProtocolLayer,
    )
    from tests.test_client import _client

    client = _client()
    client._transport = SimpleNamespace(connected=True)
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    connects, calls = [], []
    monkeypatch.setattr(client, "_connect_locked", lambda: connects.append(1))

    def operation():
        calls.append(1)
        raise NiceResponseError(
            "Rejected", layer=ProtocolLayer.NHK, code=code, request_type="INFO"
        )

    with pytest.raises(NiceBidiAuthError):
        client._run_with_reconnect(operation)
    assert len(calls) == 2 and len(connects) == 1
    calls.clear()
    connects.clear()
    with pytest.raises(NiceResponseError):
        client._run_command_once(operation)
    assert len(calls) == 1 and not connects
    assert client.session_state.state == "session_invalidated"


def test_access_mutation_uses_fresh_list_and_readback(monkeypatch):
    from tests.test_client import _client

    client = _client()
    actor = replace(ADMIN, controller_id=client.credentials.source)
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    snapshots = iter(
        [
            PairingsSnapshot((actor, PENDING)),
            PairingsSnapshot(
                (actor, replace(PENDING, role=UserRole.USER, pending=False))
            ),
        ]
    )
    monkeypatch.setattr(client, "_read_pairings_locked", lambda: next(snapshots))
    sent = []
    monkeypatch.setattr(
        client,
        "_signed_exchange_locked",
        lambda kind, body: sent.append((kind, body)) or xml(kind),
    )
    result = client.change_user(PENDING, "approve", UserRole.USER)
    assert result.principal(*PENDING.identity).role is UserRole.USER
    assert len(sent) == 1 and sent[0][0] == "USER_NEW"
    monkeypatch.setattr(
        client,
        "_read_pairings_locked",
        lambda: PairingsSnapshot((replace(actor, role=UserRole.USER), PENDING)),
    )
    with pytest.raises(NicePermissionError):
        client.change_user(PENDING, "approve", UserRole.USER)
    assert len(sent) == 1


async def test_pairing_confirmation_and_cloud_mode_never_send(hass):
    from custom_components.nice_bidiwifi.models.config import ConnectionMode

    flow = config_flow.NiceBidiConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._enrollment_exchange = AsyncMock()
    result = await flow.async_step_pairing({"confirm_enrollment": False})
    assert result["errors"]["base"] == "enrollment_confirmation_required"
    flow._connection_mode = ConnectionMode.CLOUD_ONLY
    result = await flow.async_step_pairing({})
    assert result["reason"] == "pairing_requires_local"
    flow._enrollment_exchange.assert_not_awaited()


def test_enrollment_wire_requests_are_unsigned_and_distinct(monkeypatch):
    identity = EnrollmentIdentity.new(TARGET)
    client = EnrollmentClient("192.0.2.1", 443, identity)
    sent = []
    monkeypatch.setattr(
        client,
        "_exchange",
        lambda kind, body: (
            sent.append((kind, body)) or xml(kind, '<Authentication perm="wait"/>')
        ),
    )
    client.pair_once(first_user=True, setup_code="123-456-789")
    client.pair_once()
    client.verify()
    assert " check=" in sent[0][1] and " check=" not in sent[1][1]
    assert sent[2][0] == "VERIFY" and identity.username in sent[2][1]
    assert "123-456-789" not in str(sent)


def test_router_scope_tracks_actual_active_session():
    from tests.test_connection_router import _router

    router, _health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    assert router.access_session_scope is None
    local.read_results.append("status")
    router.read_status()
    original = router.access_session_scope
    assert original[0] == "local"
    local.session_state.invalidate("3")
    assert router.access_session_scope != original
    assert set(router.session_diagnostics) == {"local", "cloud"}
    assert "username" not in str(router.session_diagnostics)


async def test_uncertain_pair_rejection_never_releases_dispatch_claim(hass):
    from custom_components.nice_bidiwifi.errors import NiceResponseError, ProtocolLayer

    flow = config_flow.NiceBidiConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._enrollment_exchange = AsyncMock(
        side_effect=NiceResponseError(
            "Timeout", layer=ProtocolLayer.NHK, code="14", request_type="PAIR"
        )
    )
    result = await flow.async_step_pairing(
        {
            "host": "192.0.2.1",
            "port": 443,
            "name": "Gate",
            "target_mac": TARGET,
            "enrollment_kind": "request_access",
            "confirm_enrollment": True,
        }
    )
    assert result["step_id"] == "pairing_wait"
    identity, state, kind = await PairingIntents(hass).get(TARGET)
    assert state == "dispatched"
    assert not await PairingIntents(hass).claim(identity)


async def test_pending_identity_cleanup_requires_exact_persisted_credentials(hass):
    from custom_components.nice_bidiwifi.models.credentials import NiceCredentials
    intents = PairingIntents(hass)
    identity, _, _ = await intents.get(TARGET)
    await intents.claim(identity)
    other = NiceCredentials('other', 'AA' * 32, TARGET, 'other')
    await intents.complete_if_bound(other)
    restored, state, _ = await intents.get(TARGET)
    assert restored == identity and state == 'dispatched'
    await intents.complete_if_bound(NiceCredentials(identity.username, 'AA' * 32, TARGET, identity.source_id))
    replacement, state, _ = await intents.get(TARGET)
    assert replacement != identity and state == 'created'


def test_unsigned_enrollment_namespace_sequence_and_no_connect(monkeypatch):
    from types import SimpleNamespace
    client = EnrollmentClient('192.0.2.1', 443, EnrollmentIdentity.new(TARGET))
    client._client._transport = SimpleNamespace(connected=True)
    sent = []
    monkeypatch.setattr(client._client, '_send_locked', lambda request, **kwargs: sent.append(request) or xml(kwargs['expected_type']))
    client._exchange('VERIFY', '<User username="u"/>')
    client._exchange('SCAN', '', protocol='WNC')
    assert 'protocolType="NHK"' in sent[0] and 'id="1"' in sent[0]
    assert 'protocolType="WNC"' in sent[1] and 'id="2"' in sent[1]
    assert all('CONNECT' not in request and 'sign=' not in request for request in sent)
    with pytest.raises(NiceParameterError):
        client._exchange('CONFIG', '', protocol='WNC')
    assert len(sent) == 2


async def test_first_user_cannot_finish_as_ordinary_user(hass):
    flow = config_flow.NiceBidiConfigFlow()
    flow.hass = hass
    flow.context = {'source': 'user'}
    flow._pair_kind = 'first_user'
    flow._pair_password = b'x' * 32
    flow._pair_role = UserRole.USER
    result = await flow.async_step_pairing_finish()
    assert result['errors']['base'] == 'first_user_admin_not_confirmed'


def test_user_readback_failure_does_not_repeat_mutation(monkeypatch):
    from tests.test_client import _client
    client = _client()
    actor = replace(ADMIN, controller_id=client.credentials.source)
    monkeypatch.setattr(client, '_ensure_connected_locked', lambda: None)
    monkeypatch.setattr(client, '_close_locked', lambda: None)
    calls = []
    monkeypatch.setattr(client, '_signed_exchange_locked', lambda kind, body: calls.append(kind) or xml(kind))
    monkeypatch.setattr(client, '_read_pairings_locked', lambda: PairingsSnapshot((actor, PENDING)))
    from custom_components.nice_bidiwifi.errors import NiceBidiConnectionError
    with pytest.raises(NiceBidiConnectionError, match='readback'):
        client.change_user(PENDING, 'approve', UserRole.USER)
    assert calls == ['USER_NEW']


async def test_verified_admin_does_not_override_explicit_device_permission_denial(hass):
    from custom_components.nice_bidiwifi.models.capabilities import NiceCapabilities
    from custom_components.nice_bidiwifi.models.device import NiceServiceCapability
    from tests.conftest import make_device_info
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.data = make_status(state='closed')
    await owner.access.async_refresh()
    caps = NiceCapabilities.from_device_info(make_device_info(), 1)
    service = NiceServiceCapability('Interface', None, 'Name', '', None, 'r', None, ())
    owner.capabilities = replace(caps, interface_name_write=True, properties=(service,))
    report = owner.features.availability('interface.name')
    assert report.permission is False
    assert not report.runtime_available
