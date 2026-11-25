"""An explicit shared-NHK CORE read adapter, separate from gate polling/control."""

from datetime import UTC, datetime

from .errors import NiceParameterError, NiceReadPreemptedError
from .protocol.nhk.core import (
    CoreTableSnapshot,
    TABLES,
    read_body,
    require_core_info,
    table_count,
    table_versions,
)


class CoreTableReader:
    def __init__(self, client):
        self.client = client

    def read(self):
        def execute():
            client = self.client
            generation = client.session_state.generation

            def read(kind, body=""):
                if client._should_yield is not None and client._should_yield():
                    raise NiceReadPreemptedError(
                        "CORE read yielded to urgent work or shutdown"
                    )
                return client._signed_exchange_locked(kind, body)

            require_core_info(read("INFO"))
            before = table_versions(read("TABLE_READ", read_body()))
            counts = {
                name: table_count(
                    read("TABLE_READ", read_body(name)), name, before[name]
                )
                for name in TABLES
            }
            after = table_versions(read("TABLE_READ", read_body()))
            if before != after or generation != client.session_state.generation:
                raise NiceParameterError(
                    "CORE table versions or session changed during inspection"
                )
            return CoreTableSnapshot(
                counts["Rules"],
                counts["Scena_In"],
                counts["Scena_Out"],
                datetime.now(UTC).isoformat(),
            )

        # Reconnect is permitted only for reads. No CoreProtocol write/execute,
        # radio binding, table edit, first setup or gate-status probe is used.
        return self.client._run_with_reconnect(execute, preemptible=True)
