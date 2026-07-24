# Optional Future Additions

This document records larger additions that are technically feasible but are
not part of the currently supported integration. They are not promises or
scheduled milestones. Work should start only when somebody requests a feature,
can provide sanitized protocol evidence, and can help test the relevant Nice
hardware.

The existing BiDi-WiFi, IT4WIFI, and CU_WIFI paths must remain isolated from
experimental CORE or installer functionality. A failure in a new protocol
adapter must never prevent an existing shared Wi-Fi entry from loading or
updating.

## Requesting one of these additions

Open a feature request and include:

- the Nice controller and child-device model names;
- firmware and hardware versions;
- whether the official app shows live state or only sends commands;
- the exact app control, state, scene, rule, or schedule being requested;
- sanitized INFO, table, status, or subscription observations where available;
- whether you can test commands while the equipment is visible and safely
  recover the installation.

Do not attach app backups, databases, credentials, access tokens, local
addresses, serial numbers, MAC addresses, raw packet captures, or unredacted
protocol output.

## Phase 7 — CORE foundation and child devices

Nice CORE uses a different protocol and device model from the shared NHK/T4
Wi-Fi accessories. A CORE installation can contain a controller plus several
child automations, inputs, outputs, sensors, rules, and scenes. Supporting it
cleanly requires a separate adapter rather than adding CORE-specific branches
to the existing client.

### Proposed implementation

1. Add a separate CORE transport, authentication, request/response, and
   subscription adapter.
2. Parse INFO plus version, device, input, output, rule, scene-input, and
   scene-output tables into typed models.
3. Normalize device status and subscription events without exposing raw
   protocol payloads to entity platforms.
4. Register one Home Assistant device for the CORE controller and one child
   device for every stable CORE device ID, linked with `via_device`.
5. Expose configuration tables, rules, and schedules read-only in diagnostics.
6. Add capability-driven entities only for validated device types:
   - gates, barriers, and garage doors as `cover`;
   - blinds and shutters as `cover`, without tilt until tilt is proven;
   - dimmers and lights as `light`;
   - ordinary on/off outputs as `switch`;
   - persistent measurements as `sensor` or `binary_sensor`;
   - remote, movement, wind, rain, and sun changes as `event`.

Normal device commands may be added when their framing and acknowledgement are
validated. “Read-only” in this phase means controller configuration remains
read-only: no rule edits, table writes, binding, or device installation.

### State rules

- Bidirectional devices may expose confirmed state after their status and
  subscription behavior is captured.
- Monodirectional devices may expose commands, but must not invent confirmed
  open, closed, on, off, position, brightness, or tilt state.
- Unknown device types remain diagnostic-only.
- One-shot input or remote events must use `event`; they must not become binary
  sensors that remain stuck on.

### Required evidence

At least one sanitized fixture is required for every enabled CORE entity
family. Coverage should include authentication, INFO, relevant tables, initial
status, subscriptions, reconnect behavior, permissions, and command
acknowledgements where commands are enabled.

## Phase 8 — CORE scenes and specialist platforms

Phase 8 builds on the stable identity, capability, status, and subscription
model from Phase 7. It maps features whose Home Assistant platforms carry
stronger physical-state expectations.

### Proposed additions

- Activate stable CORE scenes through `scene` entities.
- Use `lock` for electric locks only with reliable locked/unlocked feedback.
- Use `siren` only after activation, deactivation, and current-state semantics
  are confirmed.
- Use `valve` for irrigation only when open/closed feedback is reliable;
  otherwise retain a plain `switch`.
- Add blind tilt only when tilt position, direction, limits, and command
  acknowledgements are independently proven.
- Use `climate` only when heating mode, setpoint, current state, units, and
  availability are known; a simple heating relay remains a `switch`.
- Add read-only `calendar` entities only after recurrence, timezone,
  daylight-saving, exception, and deletion semantics are understood.
- Expand event categories for remotes, weather sensors, generic sensors, and
  device-binding activity.

### Acceptance requirements

Every specialist entity must follow its Home Assistant platform conventions.
Restored state must never be presented as current physical state unless the
device confirms it. Each command needs either a device acknowledgement and
subsequent state update, or a narrowly documented optimistic-state policy for a
device that cannot provide feedback.

Scenes, rules, and schedules remain read-only except for validated scene
activation. Creating or editing controller logic is deferred to Phase 9.

## Phase 9 — installer writes and firmware

Phase 9 is optional even if Phases 7 and 8 are completed. These operations can
change controller logic, bind physical devices, or make hardware unavailable.
Each feature should be considered independently and may be declined as not
planned.

### Possible workflows

- Create or edit rules, scenarios, schedules, and controller tables.
- Bind, add, remove, or replace devices, inputs, and outputs.
- Change installer-only controller settings.
- Expose firmware availability and installation through an `update` entity.

### Mandatory safety gates

Before any configuration write is exposed, its implementation must demonstrate:

- permission discovery and useful access-denied behavior;
- conflict detection for concurrent app or installer changes;
- atomic update behavior, or a proven rollback procedure;
- read-back verification of the complete affected object;
- bounded inputs with no arbitrary raw table, DMP, T4, or XML write surface;
- movement and physical-state interlocks appropriate to the operation;
- dedicated real-hardware fixtures, tests, and recovery documentation.

Firmware support additionally requires proof of package authenticity, signing,
model and hardware compatibility, progress reporting, cancellation behavior,
power-loss handling, failed-update recovery, and a path back to a working
controller. The integration must never accept an arbitrary firmware file.

Until every relevant gate is met, installer tables, binding, and firmware
remain unsupported.
