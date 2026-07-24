# Nice for Home Assistant

[![HACS Validation](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Jordi-14/homeassistant_nice)](https://github.com/Jordi-14/homeassistant_nice/releases/latest)

Custom Home Assistant integration for local and optional verified-relay control
of compatible Nice gates and garage doors.

The integration uses the shared Nice NHK/T4 protocol over either a local
TLS/TCP connection, the verified Nice relay, or the recommended local
connection with cloud fallback. It creates a primary `cover` plus
capability-driven control and diagnostic entities. The local endpoint on tested
BiDi-WiFi firmware uses a device certificate that cannot be validated against
Home Assistant's normal trust store, so local mode relies on LAN isolation and
NHK credentials. Relay mode uses normal public certificate and hostname
verification.

Latest stable release: `v0.7.5`

## Documentation

| Need | Document |
| --- | --- |
| Install, provision, extract credentials, and add the integration | [Setup and Credential Extraction](docs/setup.md) |
| Run capability or CU_WIFI diagnostic probes | [Diagnostic Probes](docs/probes.md) |
| Understand position, state, calibration, and the cover slider | [Position, State, and Calibration](docs/position_calibration.md) |
| Choose which entities to show or enable | [Entity Reference](entity_reference.md) |
| Install and report a prerelease | [Beta Testing](docs/beta_testing.md) |
| Understand optional CORE, specialist, installer, or firmware work | [Optional Future Additions](docs/future_additions.md) |
| Contribute new protocol findings or entities | [Contributing](CONTRIBUTING.md) |

## Features

- Open, stop, and close using the NHK `DoorAction` service.
- Automatic discovery of compatible operational Nice interfaces over zeroconf,
  including in-place address updates without duplicate devices.
- Three connection policies: recommended local + cloud fallback, fully local,
  and fully cloud. Relay TLS uses system certificate and hostname verification.
- Bounded LAN failover and hysteretic recovery avoid route flapping. Commands
  with an ambiguous outcome are never replayed on a second route.
- Native Home Assistant cover position support when the controller reports a
  numeric position source.
- Live position percentage while the gate moves when the controller exposes a
  real position source.
- On position-capable controllers, display-position animation can start
  immediately after open/close commands, then rebase to real controller
  position updates. State-only controllers never expose a synthetic position.
- Additional action buttons for partial open 1/2/3, step-step, courtesy light,
  courtesy light timer, lock, and unlock.
- Coarse set-position support by moving in the required direction and sending
  stop once the best available real or calibrated approximate target is reached.
- Optional standardized position calibration. Encoder, live-percent, and
  live-scalar position sources learn direction-specific stop correction.
  Endpoint-only devices fall back to time-based full-travel measurement, but
  this does not enable position display or set-position support.
- Real state from DMP register `04/01` when the controller exposes that path.
- Real position from DMP registers `04/11`, `04/18`, and `04/19` when encoder
  bounds are available.
- Persistent local NHK change and diagnostic event handling through a single
  connection reader. State, obstruction, live T4 position, maintenance,
  BlueBUS, battery, motor-current, and reset observations are normalized when
  the controller supplies them.
- Alternate status support from live NHK `STATUS` / `CHANGE` plus live T4
  `04/40` and `04/02` events, including CU_WIFI percentage frames and
  RBA4R10-style raw scalar position frames.
- Faster polling while the gate is moving, slower polling while idle.
- Adaptive polling remains active as a fallback if the unsolicited event stream
  is interrupted.
- Automatic reconnect after BiDi reboot, HA restart, and transient TLS EOFs.
- Diagnostic sensors for connection state, last update/error, reconnect count,
  command latency, encoder calibration values, and device firmware/serial data.
- Diagnostic buttons to refresh status immediately or force a local reconnect.
- A Home Assistant event entity plus capability-adaptive event diagnostics.
  Raw frames and device MAC addresses are not exposed as entity attributes.
- Optional shared Wi-Fi administration entities for bounded event-log retrieval,
  count-only access-group inspection, interface naming, clock synchronization,
  and interface reboot. They are capability-gated and disabled by default.

## BusT4 Diagnostics

The `0.7.0` release adds diagnostic and configuration entities based on the
broader BusT4/OXI register map seen in
[ngem1/esphome-nice-bidiwifi](https://github.com/ngem1/esphome-nice-bidiwifi)
and community testing.

The integration can expose controller configuration/status sensors for partial
open positions, speed, force, pause time, maintenance counters, stop reasons,
raw diagnostics, OXI/radio metadata, and several BusT4 settings. Writable
BusT4 entities are advanced controls: only change values while the gate is
visible and you can recover the original settings.

The broader diagnostic scan runs at startup and then at most every 5 minutes
while the gate is idle. Core cover state still uses the normal status path every
refresh. The integration does not write controller configuration registers
unless one of the BusT4 configuration entities is changed manually.

See [Entity Reference](entity_reference.md) for the full entity list, visibility
defaults, and safety notes.

## Shared Wi-Fi administration

BiDi-WiFi, IT4-WiFi, and CU_WIFI interfaces can expose the same administration
requests used by the official app. The optional entities are all disabled by
default:

- Log retrieval requests at most 32 interface and 32 automation events. Only
  reviewed event type, time, action, status, and obstruction fields are kept.
  Raw XML, names, locations, source identifiers, device identifiers, and
  arbitrary values are discarded.
- Access groups are read-only. Home Assistant exposes only the number of groups,
  devices, and rules; it does not retain group IDs, device IDs, permission IDs,
  or permission values. Group and rule editing is intentionally not implemented.
- Interface-name changes accept 1–64 printable characters and are confirmed by
  reading INFO back from the interface.
- Clock synchronization writes UTC time, the standard timezone offset, and the
  current DST offset separately, then confirms all three through INFO.
- Interface reboot is sent once. If its acknowledgement is lost, the command is
  not retried because the interface may already be rebooting.

Name, clock, and reboot changes are rejected while the gate is moving. Home
Assistant diagnostics include the inferred capability basis, safety policy,
bounded log/group snapshots, and the last 16 administration outcomes with
route, latency, verification method, safe failure class, and protocol error
code. Operation values and raw protocol data are never placed in that history.

## Compatibility

Known working setup:

- BiDi-WiFi firmware: `2.6.4`
- BiDi-WiFi hardware: `SB725A1-R0-R01`
- Tested control unit family: Nice Robus sliding gate controller family
- Tested control unit firmware: `FG01h`
- Home Assistant: `2024.11.0` or newer

This integration was originally tested with BiDi-WiFi devices and depends on the
local NHK/T4/DMP protocol surface, which is not publicly documented by Nice.
Some devices reporting `interface_product: CU_WIFI` expose enough of the same
local NHK/T4 command surface for open, stop, and close. Newer beta builds also
include experimental CU_WIFI status support from live NHK and T4 events, but
CU_WIFI position may be coarser and less frequent than the encoder-derived DMP
position available on the originally tested BiDi-WiFi setup.

If you rely on this integration, we recommend not updating the BiDi-WiFi
firmware beyond `2.6.4` unless you are prepared to retest local control and
recover using the official Nice app or remote. A firmware update could change
authentication, command framing, register layout, or local TCP 443 behavior and
break the integration.

## Requirements

- Fully local and local-with-cloud-fallback entries need LAN reachability to the
  Nice interface on TCP 443.
- Fully cloud and fallback entries need outbound reachability to the configured
  Nice relay with its public TLS certificate and hostname intact.
- The Nice interface should keep its normal network/cloud configuration.
- MyNice/MyNice Pro should be closed while Home Assistant is using local
  control.
- Network ACLs must allow only the routes selected for the entry.
- No local IP address, MAC address, username, source/controller ID, or password
  should be shared on GitHub.

If Home Assistant and the BiDi-WiFi are on different VLANs, the firewall must
allow Home Assistant to initiate TCP 443 connections to the BiDi-WiFi.

## Installation

### HACS Custom Repository

Until this integration is accepted into the HACS default repositories, add it as
a custom HACS integration repository:

```text
https://github.com/Jordi-14/homeassistant_nice
```

Category:

```text
Integration
```

Download it through HACS, restart Home Assistant, then add **Nice** from
**Settings -> Devices & services**.

Prereleases are available for deliberate testing. See
[Beta Testing](docs/beta_testing.md) before installing one.

### Manual

Copy this folder into your Home Assistant config directory:

```text
custom_components/nice_bidiwifi
```

Then restart Home Assistant.

## Quick Setup

1. Configure the BiDi-WiFi with the normal **MyNice** app.
2. Let Home Assistant discover the interface, or find its address for manual
   setup. A DHCP reservation is optional when zeroconf works across the network.
3. Confirm Home Assistant can reach the interface on TCP 443.
4. Enter NHK device credentials manually, or use the optional one-time MyNice
   import with OAuth application credentials you are authorized to use.
5. Select the discovered **Nice** card, or add **Nice** manually from
   **Settings -> Devices & services**.
6. Close MyNice/MyNice Pro before submitting the config flow.

The recommended mode uses the LAN while it is healthy, fails over to the Nice
relay after bounded connection failures, and returns only after repeated
successful LAN probes. Fully local never opens a cloud connection. Fully cloud
never opens a LAN connection. The account password, OAuth client credentials,
and access token used by the optional import are not stored; the imported NHK
device credentials are stored because both local and relay sessions require
them.

Detailed setup and credential extraction instructions are in
[Setup and Credential Extraction](docs/setup.md).

## Position and Calibration Summary

Real position can come from encoder registers or validated live controller
frames. Endpoint-only devices can safely report open, closed, opening, closing,
or stopped. They expose no Home Assistant position until the controller
supplies a real numeric position source; endpoint states are not converted to
`0%` or `100%`.

The cover attributes separate real source data from dashboard display values:

- `real_position`: latest real percentage from the device, if available.
- `display_position`: value shown by the cover and `Gate position` sensor.
- `display_position_estimated`: `true` when the display value is simulated or
  held from the last known value.
- `position_simulation_action`: simulated display direction while active.

Calibration is optional. Encoder, live-percent, and live-scalar sources can
learn stop correction for intermediate targets. Endpoint-only devices can
measure full-travel timing for diagnostics, but the integration does not turn
that timing into Home Assistant position data or set-position support.

See [Position, State, and Calibration](docs/position_calibration.md) for the
full behavior, calibration sequence, known registers, and dashboard slider
example.

## Which Entities Should I Use?

For a normal dashboard, start with the `cover` entity. It is the primary Home
Assistant entity for open, close, stop, current position, and set-position when
position data is available.

Good dashboard candidates:

| Entity | Use it for |
| --- | --- |
| Gate cover | Daily open, stop, close, and position control. |
| Gate position | Same displayed position used by the cover card; real only when fresh controller position data is available. |
| Step-step | Normal remote-control style action. |
| Partial open 1/2/3 | Pedestrian, delivery, or vehicle-width openings. |
| Courtesy light / timer | Only when the control unit has a courtesy light output wired and configured. |
| Connection and route state | Overall health, active local/cloud route, per-route reachability, and last successful update. |

`Hidden` in Home Assistant does not mean broken or unavailable. In this
integration it usually means the entity is diagnostic, advanced, or not normally
needed on dashboards.

Advanced BusT4 settings can change controller behavior such as speed, force,
auto close, pause time, and partial-open positions. Check the original values
before changing them and only change settings while the gate is visible.

See [Entity Reference](entity_reference.md) for the complete entity table.

## Diagnostics and Support

For setup diagnostics, see [Setup and Credential Extraction](docs/setup.md).

For compatibility reports and CU_WIFI status investigations, use the read-only
scripts in [Diagnostic Probes](docs/probes.md). Public reports should use the
default redacted output and must not include credentials, local IPs, MAC
addresses, serial numbers, app backups, SQLite databases, or packet captures.
For shared Wi-Fi administration issues, include Home Assistant diagnostics
after reproducing the problem. The administration section identifies the route,
capability decision, safety block, latency, verification result, and bounded
device observations without exposing raw payloads or local access-group data.

## Contributing

The BiDi-WiFi can do more than this integration currently exposes. Additional
MyNice Pro controls, read-only sensors, and configuration settings can be added
after their local command names or T4/DMP frames are confirmed and tested
safely.

To map a new MyNice Pro control or diagnostic value, follow the discovery and
capability-capture workflow in [CONTRIBUTING.md](CONTRIBUTING.md).

Larger optional additions for CORE controllers, specialist platforms,
installer workflows, and firmware are documented in
[Optional Future Additions](docs/future_additions.md). They are feasible future
work, not scheduled commitments, and require a feature request plus sanitized
real-device evidence.

## Safety

This controls a physical gate. Test while the gate is visible and keep the
original Nice remote/app available.

Do not publish app-data backups, SQLite databases, packet captures, extracted
passwords, source IDs, MAC addresses, serial numbers, or local IP addresses.
