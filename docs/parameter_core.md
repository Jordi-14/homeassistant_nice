# Parameter and command core

The first refactor milestone introduces one per-entry parameter repository and a
common command-result boundary. It keeps the existing entity identities, public
raw-value number API, local/cloud policy and TLS implementation.

## Parameters

`ParameterRepository` is shared by the coordinator, router and route clients.
`ParameterAddress` includes the selected device, BusT4 address/endpoint, register
and optional index. Another device cannot populate the repository's metadata;
another bus target does not inherit the controller's parameter definitions.

Each record retains raw INFO, decoded metadata, the latest raw value, its decoded
value where verified, capture time and source. INFO, GET and SET support are
tracked independently. Unavailable INFO does not imply that GET is unsupported.
A bus timeout does not establish that metadata is unsupported.

The existing reviewed settings catalog now supplies backend bounds, value kinds,
units and dynamic bounds. `prepare_write` checks the catalog, controller policy,
shape, width, byte order, enum options, range and step. It produces a frozen,
scoped `PreparedParameterWrite` containing the exact wire payload. The client
sends that payload rather than reconstructing the application-level value.

The unsigned scalar formats already verified for the current catalog are
supported. Other formats retain their raw metadata and bytes without inventing a
numeric interpretation or enabling writes. Signedness is explicitly unknown for
unverified formats. Engineering-unit conversion is available in the core, while
existing HA numbers retain their raw-value interface until the presentation
milestone.

Indexed reads preserve the INFO index width and validate the returned index.
Scalar writes to any known indexed setting are blocked, including one-element
arrays. Once an indexed format is seen, a later missing INFO cannot unlock a
scalar write during that runtime. MC824H/HYKE force writes remain blocked without
valid non-indexed metadata. No motor/index mapping is inferred and no new
per-motor configuration controls are exposed by this milestone.

## Execution and responses

Door actions, T4 actions and parameter writes use `CommandExecutor` for one
dispatch and a normalized result. The router retains the existing no-replay rule
for writes. A configuration write rechecks movement, calibration and metadata
after acquiring the protocol session, immediately before dispatch. It is refused
if those local observations no longer allow the prepared operation.

Addressed DMP replies are checked for source/receiver, register, operation,
lengths, checksums and status. Explicit controller errors inside a successful NHK
envelope reach the caller as structured errors. The protocol layer and operation
remain available to diagnostics; DMP codes are not interpreted as NHK codes.
Research scripts retain their legacy raw decoder for inspecting captures.

An acknowledgement describes the protocol result, not physical arrival:

- `accepted`: a positive acknowledgement was received;
- `rejected`: an explicit rejection or local validation failure occurred;
- `unknown`: transmission or execution cannot be established, including a bus
  timeout. The command is not automatically replayed.

A DMP response without a controller acknowledgement remains `unknown`; a
subsequent GET may still verify that the desired setting is present.

## Targeted verification

A setting write is followed by one GET of that setting. A read may retry under
the existing read policy, but the SET is never repeated for verification.
Diagnostics record `verified`, `unavailable` or `mismatch` separately from the
write acknowledgement. A matching read confirms the requested setting value,
not that the write caused a change from a different value.

A mismatch is reported to the caller without treating it as a broken connection.
A readback connection failure retains the write outcome and marks the connection
failure separately. A readback preempted by a pending command leaves verification
unavailable. A late readback cannot overwrite a newer command or configuration
snapshot. Changing one setting no longer requests a complete extended scan.

The parameter-to-status bindings are shared by decoding and cache merging.
Fresh diagnostic fields survive the merge along with their raw registers. Full
per-field poll/event reconciliation and component state ownership belong to the
next milestone.

## Compatibility and diagnostics

No existing entity IDs, calibration storage or movement profiles are migrated.
The IT4WIFI T4Action guard remains in place, and TLS shutdown/serialization is
unchanged. The parameter diagnostics include metadata, support, raw observations,
source and timestamps. Password and credential register values are excluded.

Validation covers captured INFO packets, synthetic positive/negative replies,
wrong targets and operations, malformed metadata, widths and byte orders,
indexed values, uncertain delivery, readback failures and concurrent commands.
These tests do not substitute for validation on every controller model. Normal
use on the beta is useful; new calibration, force adjustments or deliberate
obstructions are not required for testing this internal refactor.
