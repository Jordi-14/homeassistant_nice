# Runtime and extension contracts

Phase 2 changes the shared runtime while preserving entity unique IDs, storage keys, calibration schemas, route selection, TLS settings and existing UI controls. It does not enable every app feature. See [app parity](app_parity.md) and [evidence gaps](protocol_gaps.md).

## State ownership

The coordinator orchestrates components. The connection runtime owns health and connection state; the command executor owns command outcomes; position owns separate estimator, animation and target-control state; calibration owns HA calibration; events own stream state and event history; administration owns its snapshots; diagnostics have their own state.

`CoordinatorCompatibility` explicitly lists the existing facade properties and methods used by HA entities and older callers. There is no catch-all attribute forwarding. New internal code addresses the component that owns the state.

`ObservationStore` is the measurement authority. Polls take a revision and connection-generation token before I/O. A later live update wins independently for each field. A reply from an invalidated generation is discarded. Omitted fields remain cached with their original provenance and age; an explicitly observed unknown value invalidates that field. Display animation and local stop hints are applied afterward and never enter the measurement store. Configuration write safety uses reported movement rather than the display's temporary stop hint.

Only fresh numeric measurements feed speed learning, using their observation timestamps. Directional band learning and the 90% display factor are retained. Cached diagnostics, animated percentages and repeated state-only updates cannot teach a new speed. Existing confirmed forward jumps, monotonic display during continuous movement, and reconciliation at stops/reversals are retained.

## Scheduling and lifecycle

One session scheduler serializes both routes. Priorities are Stop, user controls, readback, status, diagnostics, then metadata. Equal-priority operations are FIFO. A wire exchange already in progress finishes safely; preemptible reads yield between registers or before reconnecting. An explicitly non-preemptible read retains its contract.

User commands have a 15-second queue-and-connect deadline, checked again immediately before sending. Expired commands report `delivery=not_sent`; they cannot remain in a queue and execute later. Ambiguous writes are never replayed. Background lock acquisition is bounded to 30 seconds. Network exchanges retain their existing transport timeouts.

Runtime tasks are tracked, including child refreshes. Reconnect invalidates old poll results, cancels owned work and starts a fresh observation cycle. Shutdown fences observations, unsubscribes event callbacks, closes admission to the scheduler and cancels runtime tasks. An exchange already running releases and closes its clients safely. A cancelled executor future does not falsely imply its underlying thread stopped: scheduler admission and dispatch guards enforce that boundary.

## Capability explanations

`coordinator.capability_report` and integration diagnostics provide stable feature IDs and independent implementation, hardware support, permission and runtime availability fields. Unknown support or permission remains unknown. Existing compatibility actions can reconnect on demand and retain their existing advertisement rules. Controller-specific restrictions and the parameter engine remain authoritative for writes.

Use `await coordinator.async_execute_feature("automation.stop")` or a reviewed feature ID. The extension boundary validates the definition before task cancellation, queuing, metadata reads or any network activity. This is an internal extension API, not a new arbitrary-code or raw-register HA service.

CORE and ProView have explicit blocked access adapters. They do not guess a transport or instantiate a network connection. Shared Wi-Fi continues using the reviewed local/relay router.

## Adding a discovered binding

1. Find the source inventory item and its gap record. Verify the actual call chain, target model, transport, account permissions and firmware scope.
2. Add a reviewed codec/profile with sanitized request and reply fixtures. Do not use app enum IDs as opcodes. Preserve unknown values as `None`; zero can be a valid known value.
3. Fill the typed binding slots and add an allowlisted handler using the existing router, parameter validation and command executor. Never dynamically dispatch a handler named by app data.
4. For a procedure, verify its complete state machine, progress, completion, abort and recovery. `ProcedureDefinition.preflight` checks all prerequisites, steps and recovery bindings before the runner can start. One recovered start command is insufficient.
5. Test denied permissions, unsupported hardware, unresolved slots, timeout, cancellation, malformed replies, wrong-device scope, ambiguous delivery and recovery. A blocked path must have zero cancellations, queue entries and I/O.
6. Update the catalog and gaps, then run `python scripts/render_capabilities.py` and `python scripts/render_capabilities.py --check` with test dependencies installed.

The source index is generated with `scripts/inventory_apps.py --welcome PATH --pro PATH --output custom_components/nice_bidiwifi/app_inventory.json`. It reads exported text only. It never runs research scripts, app code or gate commands. The vendor sources and user archives are not bundled.
