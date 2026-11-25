"""CORE read-only source contract: table versions, bounds and private-row suppression."""

import pytest

from custom_components.nice_bidiwifi.core_inspection import CoreTableReader
from custom_components.nice_bidiwifi.errors import NiceParameterError, NiceProtocolError
from custom_components.nice_bidiwifi.protocol.nhk.core import (
    TABLES,
    read_body,
    table_count,
    table_versions,
)
from tests.test_client import _client


def versions(value="1"):
    return (
        '<Response type="TABLE_READ"><Tables>'
        + "".join(f'<Table id="{name}" ver="{value}"/>' for name in TABLES)
        + "</Tables></Response>"
    ).encode()


@pytest.mark.parametrize(
    "table",
    [
        "Devices",
        "Input",
        "Output",
        "Schedule",
        "TABLE_WRITE",
        "RUN_COMMAND",
        "<inject/>",
    ],
)
def test_only_inspected_table_names_are_encodable(table):
    with pytest.raises(NiceParameterError):
        read_body(table)


def test_rules_and_scene_rows_are_counted_without_private_content():
    frame = b'<Response type="TABLE_READ"><Tables><Table id="Rules" ver="1"><Rule id="private" uid="person" status="1" id_out="secret-action"/></Table></Tables></Response>'
    assert table_count(frame, "Rules", "1") == 1
    assert table_versions(versions()) == dict.fromkeys(TABLES, "1")
    with pytest.raises(NiceParameterError):
        table_count(frame, "Rules", "2")
    with pytest.raises(NiceProtocolError):
        table_count(frame, "Scena_In", "1")


def test_empty_is_only_established_with_an_explicit_matching_table_and_version():
    frame = b'<Response type="TABLE_READ"><Tables><Table id="Rules" ver="1"/></Tables></Response>'
    assert table_count(frame, "Rules", "1") == 0
    for wrong in [
        b'<Response type="TABLE_READ"/>',
        b'<Response type="TABLE_READ"><Tables><Table id="Rules"/></Tables></Response>',
    ]:
        with pytest.raises(NiceProtocolError):
            table_count(wrong, "Rules", "1")


def test_core_reader_uses_no_gate_status_or_write_requests(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    calls = []

    def exchange(kind, body=""):
        calls.append((kind, body))
        if kind == "INFO":
            return b'<Response type="INFO"><Interface><Prod>CORE</Prod><SerialNr>secret</SerialNr></Interface></Response>'
        if body == read_body():
            return versions()
        table = next(name for name in TABLES if f'id="{name}"' in body)
        tag = TABLES[table]
        return f'<Response type="TABLE_READ"><Tables><Table id="{table}" ver="1"><{tag} id="private" desc="secret" list="private-remote"/></Table></Tables></Response>'.encode()

    monkeypatch.setattr(client, "_signed_exchange_locked", exchange)
    result = CoreTableReader(client).read().export()
    assert result["rules"] == result["scene_inputs"] == result["scene_outputs"] == 1
    assert (
        result["table_versions_consistent"] and not result["device_execution_confirmed"]
    )
    assert not any(x in str(result) for x in ["secret", "private-remote"])
    assert len(calls) == 6 and {kind for kind, _ in calls} == {"INFO", "TABLE_READ"}


def test_gate_endpoint_is_rejected_before_any_table_query(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    calls = []

    def exchange(kind, body=""):
        calls.append(kind)
        return b'<Response type="INFO"><Interface><Prod>BIDIWIFI</Prod></Interface></Response>'

    monkeypatch.setattr(client, "_signed_exchange_locked", exchange)
    with pytest.raises(NiceParameterError):
        CoreTableReader(client).read()
    assert calls == ["INFO"]


def test_version_changes_do_not_publish_inconsistent_counts(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    version_calls = []

    def exchange(kind, body=""):
        if kind == "INFO":
            return b'<Response type="INFO"><Interface><Prod>CORE</Prod></Interface></Response>'
        if body == read_body():
            version_calls.append(1)
            return versions(str(len(version_calls)))
        table = next(name for name in TABLES if f'id="{name}"' in body)
        return f'<Response type="TABLE_READ"><Tables><Table id="{table}" ver="1"/></Tables></Response>'.encode()

    monkeypatch.setattr(client, "_signed_exchange_locked", exchange)
    with pytest.raises(NiceParameterError):
        CoreTableReader(client).read()
    assert len(version_calls) == 2
