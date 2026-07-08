# Nice for Home Assistant

[![HACS Validation](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Jordi-14/homeassistant_nice)](https://github.com/Jordi-14/homeassistant_nice/releases/latest)

Custom Home Assistant integration for local control of compatible Nice gates
and garage doors.

This integration talks directly to compatible local NHK/T4 services over
TLS/TCP 443 and creates one `cover` entity plus helper diagnostic entities.

The local TLS endpoint on tested BiDi-WiFi firmware uses a device certificate
that cannot be validated against Home Assistant's normal trust store. The
integration therefore keeps certificate verification disabled for this local
socket and relies on LAN isolation plus the NHK credentials for access control.

Latest stable release: `v0.7.0`

## Features

- Open, stop, and close using the local `DoorAction` service.
- Native Home Assistant cover position support.
- Live position percentage while the gate moves.
- Estimated cover-position animation starts immediately after open/close
  commands, then rebases to real BiDi-WiFi position updates as they arrive.
- Additional action buttons for partial open 1/2/3, step-step, courtesy light,
  courtesy light timer, lock, and unlock.
- Coarse set-position support by moving in the required direction and sending
  stop once the target percentage is reached or crossed.
- Optional position calibration. Devices with encoder data move through
  20/40/60/80% targets, learn direction-specific stop correction, and
  interpolate between calibrated points. Devices without encoder data fall back
  to time-based full-travel calibration for better display animation and
  approximate set-position timing.
- Calibration measures three full openings and three full closings without
  encoder data, selects the median measured duration for each direction, and
  uses 80% of the selected direction speed for display animation. Without
  calibration, display animation falls back to 1% per second.
- Detailed calibration quality report with per-target attempts when encoder
  data is available, full-travel timing, command latency, movement timing, and
  event logs.
- Real state from DMP register `04/01` when the controller exposes that path.
- Real position from DMP registers `04/11`, `04/18`, and `04/19` when encoder
  bounds are available.
- Experimental CU_WIFI status support from live NHK `STATUS` / `CHANGE` plus
  live T4 `04/40` and `04/02` events, with cached/estimated display position
  used when CU_WIFI only reports sparse coarse percentages.
- Faster polling while the gate is moving, slower polling while idle.
- Automatic reconnect after BiDi reboot, HA restart, and transient TLS EOFs.
- Diagnostic sensors for connection state, last update/error, reconnect count,
  command latency, encoder calibration values, and device firmware/serial data.
- Diagnostic buttons to refresh status immediately or force a local reconnect.

### 0.7 BusT4 Diagnostics

The `0.7.0` release adds diagnostic and configuration entities based on the
broader BusT4/OXI register map seen in
[ngem1/esphome-nice-bidiwifi](https://github.com/ngem1/esphome-nice-bidiwifi)
and community testing:

- Controller configuration/status sensors for max open, partial-open positions,
  opening/closing speed, opening/closing force, pause time, maintenance
  threshold, maintenance count, total maneuver count, last stop reason, and raw
  diagnostic bytes.
- Binary diagnostic sensors for limit switches, photocell, obstacle detection,
  input 1-4 enabled flags, auto close, photo close, always close, standby,
  pre-flash, key lock, and OXI receiver detection.
- OXI/radio metadata sensors for product, firmware, hardware, and description
  when the radio endpoint answers locally.
- Writable BusT4 configuration entities for auto close, photo close, always
  close, standby, pre-flash, key lock, opening/closing speed/force, pause time,
  photo/always-close timing and modes, partial-open positions, and maintenance
  threshold.

The new BusT4 registers are read-only and optional. Core cover state still uses
the existing DMP status registers every normal refresh; the broader diagnostic
scan runs at startup and then at most every 5 minutes while the gate is idle,
with cached diagnostic values reused between scans. The integration does not
write controller configuration registers such as speed, force, auto-close
settings, or partial-open positions unless one of the BusT4 configuration
entities is changed manually.

The BusT4 configuration entities use DMP `RUN_SET` writes. The single-byte
switches and number entities mirror the write shape used by
`ngem1/esphome-nice-bidiwifi`; partial-open positions and maintenance threshold
use two-byte big-endian payloads matching the values read from those registers.
Mode registers are exposed as raw bytes because tested controllers can report
values outside the initially assumed small enum range.
Treat this as advanced functionality: only change values while the gate is
visible and you can recover the original settings. The writable BusT4 entities
are unavailable while the gate is moving.

The `04/D1` diagnostics byte is also exposed raw for comparison. Its decoded
limit-switch and photocell bits are experimental: on the tested NewRobus
`FG01h`, the byte stayed `0x40` when fully closed, half-open, and fully open, so
those decoded entities should not be trusted for that exact gate. They are kept
because the same byte may still be meaningful on other BusT4 controllers.

### Which Entities Should I Use?

For a normal dashboard, start with the `cover` entity. It is the primary Home
Assistant entity for open, close, stop, current position, and set-position when
position data is available. The separate gate `switch` is a simpler duplicate
control for users who prefer an on/off style entity: `on` means the gate is not
closed, turning it on opens, and turning it off closes.

`Hidden` in Home Assistant does not mean broken or unavailable. In this
integration it usually means the entity is diagnostic, advanced, or not normally
needed on dashboards. Hidden entities are still created and updated unless they
are explicitly disabled in Home Assistant's entity registry.

Good dashboard candidates:

| Entity | Use it for |
| --- | --- |
| Gate cover | Daily open, stop, close, and position control. |
| Gate position | Showing the same displayed position used by the cover card. It is real when fresh position data is available and cached/estimated when the cover marks `display_position_estimated`. |
| Step-step | Mimicking a normal remote-control button that cycles through the controller's configured step-step behavior. |
| Partial open 1/2/3 | Pedestrian, delivery, or vehicle-width openings, depending on how the controller positions are configured. |
| Courtesy light / timer | Only when the control unit has a courtesy light output wired and configured. |
| Connection state / last successful update | Basic health checks for the local BiDi-WiFi connection. |

Advanced controls and settings:

| Entity group | What it does | Notes |
| --- | --- | --- |
| Auto close and pause time | Closes automatically after the gate opens and the pause time expires. | Check the current values before changing them. |
| Photo close and photo close time | Closes after the photocell has been interrupted and cleared. | Behavior depends on the control unit and photocell wiring. |
| Always close and always close time | Lets the controller recover by closing after power return or an open-state check. | Use only if you want that recovery behavior. |
| Opening/closing speed and force | Changes motor movement characteristics. | Safety-sensitive; do not increase force to hide mechanical friction. |
| Partial-open position settings | Changes the encoder targets used by Partial open 1/2/3. | Values are raw encoder positions between closed and open. |
| Maintenance threshold | Changes the maneuver count threshold used for maintenance warnings. | Does not reset the maintenance count. |
| Standby, pre-flash, and key lock | Changes controller features around energy saving, warning flash, and local key/button lock. | These are installer-style controller settings. |
| Photo close mode and always close mode | Writes raw controller bytes that the integration does not decode. | Strongly recommended: do not change these unless you already know the exact byte your controller expects. A wrong raw mode byte may leave the feature misconfigured. |

Entities ending in `setting` are writable BusT4 configuration entities. The
matching entities without `setting` are read-only views of the current
controller values. Before changing a writable setting, note the original value
and make the change only while the gate is visible and safe to operate.
The mode setting entities are a special case: they are exposed only so existing
controller values can be inspected and restored, not because their byte values
are understood by the integration.

## Compatibility

Known working setup:

- BiDi-WiFi firmware: `2.6.4`
- BiDi-WiFi hardware: `SB725A1-R0-R01`
- Tested control unit family: Nice Robus sliding gate controller family
- Tested control unit firmware: `FG01h`
- Home Assistant: `2024.11.0` or newer

This integration was originally tested with BiDi-WiFi devices and depends on
the local NHK/T4/DMP protocol surface, which is not publicly documented by
Nice. Some devices reporting `interface_product: CU_WIFI` expose enough of the
same local NHK/T4 command surface for open, stop, and close. Newer beta builds
also include experimental CU_WIFI status support from live NHK and T4 events,
but CU_WIFI position may be coarser and less frequent than the encoder-derived
DMP position available on the originally tested BiDi-WiFi setup.

If you rely on this integration, we recommend not updating the BiDi-WiFi
firmware beyond `2.6.4` unless you are prepared to retest local control and
recover using the official Nice app or remote. A firmware update could change
authentication, command framing, register layout, or local TCP 443 behavior and
break the integration.

## Requirements

- The BiDi-WiFi must be reachable from Home Assistant on TCP 443.
- The BiDi-WiFi should keep its normal network/cloud configuration.
- MyNice/MyNice Pro should be closed while Home Assistant is using local control.
- Network ACLs must allow Home Assistant to reach the BiDi IP on TCP 443.

If Home Assistant and the BiDi-WiFi are on different VLANs, the firewall must
allow Home Assistant to initiate TCP 443 connections to the BiDi-WiFi. No local
IP address, MAC address, username, or password should be shared on github.

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

Download it through HACS, restart Home Assistant, then add **Nice**
from **Settings -> Devices & services**.

This repository is being prepared for inclusion in the HACS default
repositories. Until that is merged, use the custom repository flow above.

### Manual

Copy this folder into your Home Assistant config directory:

```text
custom_components/nice_bidiwifi
```

Then restart Home Assistant.

## Configure the BiDi-WiFi

This integration expects the BiDi-WiFi to be configured with the normal
**MyNice** app and connected to the same network that Home Assistant can reach.
The installer-focused **MyNice Pro** app can be useful for diagnostics, but the
credential extraction flow below uses the normal MyNice app data.

1. Install and power the BiDi-WiFi normally.
2. Reset or provision the BiDi-WiFi if it is not already joined to your home
   network.
3. Connect the iPhone to the Wi-Fi network you want the BiDi-WiFi to join.
   MyNice provisions the BiDi-WiFi onto the network the iPhone is currently
   using; it does not ask you to pick a different target network later.
4. Open **MyNice** on the iPhone.
5. Add/configure the BiDi-WiFi interface in MyNice.
6. When MyNice asks, connect the iPhone to the temporary BiDi access point. It
   is usually named like:

   ```text
   NiceBIDIWIFIxxxxxx_AP
   ```

7. Follow the MyNice provisioning flow. The BiDi-WiFi should join the Wi-Fi
   network that the iPhone was using before connecting to the temporary access
   point.
8. Put the iPhone back on that normal Wi-Fi and confirm that MyNice can still
   control the gate.
9. Find the BiDi-WiFi IP address in your router, DHCP server, or network
   controller.
10. Reserve that IP address in DHCP so Home Assistant keeps using the same
    address.
11. From a machine on the same network as Home Assistant, confirm TCP 443 is
    reachable:

    ```bash
    nc -vz <bidi_ip> 443
    ```

12. If Home Assistant and the BiDi-WiFi are on different VLANs, allow Home
    Assistant to initiate TCP 443 connections to the BiDi-WiFi.

Close MyNice/MyNice Pro before adding the integration to Home Assistant. The
BiDi-WiFi can be sensitive to concurrent local sessions.

## Extract Credentials

The integration needs the local credential stored by the normal MyNice app.

### iPhone

1. Connect the iPhone to your Mac over USB and trust the Mac if prompted.
2. Use iMazing, or another iOS app-data backup tool, to make a backup of the
   **MyNice** app data.
3. Export the MyNice app-data backup. iMazing may create a `.imazingapp` or
   `.imazing` file instead of an extracted folder; that file is the app-data
   export, not the SQLite database itself.
4. Run the extractor from this repository against the iMazing export file, the
   extracted app-data folder, or the SQLite database if you have already found
   it:

```bash
python3 scripts/extract_mynice_credentials.py "path/to/MyNice.imazingapp"
```

The extractor searches for the credential database automatically. In known iOS
exports the database is usually here:

```text
Container/Library/Application Support/CachedData.sqlite
```

You can also run the extractor against that file directly:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/Container/Library/Application Support/CachedData.sqlite"
```

If you have more than one BiDi-WiFi stored in MyNice, pass the BiDi MAC address
to select the right one:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/MyNice.imazingapp" \
  --mac "AA:BB:CC:DD:EE:FF"
```

If the exported app data only contains `nice.log`, the export does not include
the MyNice private app container needed by this integration. Open MyNice, verify
it can control the BiDi-WiFi, close MyNice completely, then create a fresh
iMazing app-data backup of the **MyNice** app rather than browsing the app's
file-sharing documents. If the fresh export still has no SQLite database, open
an issue with the iOS version, MyNice version, iMazing version, and the file
names present in the export. Do not attach the backup or database.

The extractor prints JSON similar to this:

```json
{
  "maintenance_state": 0,
  "password": "64_HEX_CHARACTERS",
  "permission": 1,
  "source_id": "ios_app_example",
  "target_mac": "AA:BB:CC:DD:EE:FF",
  "username": "example_user"
}
```

Use these values in Home Assistant:

- `target_mac` -> **BiDi MAC address**
- `username` -> **NHK username**
- `password` -> **NHK password hex**
- `source_id` -> **Source/controller ID**

The `permission` and `maintenance_state` fields are informational and are not
entered in Home Assistant.

Treat the password as a gate-control secret.

Home Assistant diagnostics redact the configured host, MAC address, username,
password, source/controller ID, serial numbers, and raw register details before
export.

## Capability Probe

For development, the repository includes a local probe that dumps the
BiDi-WiFi `INFO` service tree plus the status registers currently used by the
integration. Use it after provisioning the BiDi-WiFi and extracting credentials:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/MyNice.imazingapp" \
  > credentials.json

python3 scripts/dump_bidi_capabilities.py \
  --host <bidi_ip> \
  --credentials credentials.json \
  --include-raw-info \
  --output bidi_capabilities.json
```

The probe does not move the gate. It sends an authenticated `INFO` request and,
unless `--skip-status` is used, reads the same DMP status registers used by the
integration for state and position.

The generated report redacts host, MAC address, username, source/controller ID,
and serial numbers by default. The NHK password is never written to the report.
Do not publish `credentials.json`.

### CU_WIFI Status Probe

For CU_WIFI / Robus devices where commands work but DMP status returns `Code 14`,
use the live read-only probe:

```bash
python3 scripts/probe_cuwifi_status.py \
  --host <cuwifi_ip> \
  --credentials credentials.json \
  --manual-stop \
  --exhaustive \
  --output cuwifi_status_probe_manual_exhaustive.json
```

Start the script first, then move the gate with the normal remote or MyNice app
through the states you want to capture. Press `Ctrl-C` once when the actions are
finished; the probe treats that as the end of the live capture and still writes
the report. The probe authenticates locally, keeps one session open, listens for
async frames, and polls read-only `STATUS`, `T4_STATUS`, and `INFO`. It does not
send `CHANGE`, `DEP`, open, stop, close, or partial-open commands.

After the live capture it also records the current integration DMP status path
and read-shaped NHK selector probes. With `--exhaustive`, it also runs the
broadest read-only post-live scan currently known: controller, OXI/radio,
status, position, diagnostics, and `GET` selector candidates. Do not use
`--include-sensitive` for reports shared publicly.

This probe is still useful when a CU_WIFI beta mostly works but state,
position, or obstruction behavior does not match the physical gate. It captures
the live NHK/T4 frames the integration relies on for the CU_WIFI fallback path.

### Android

The Android app stores the same local NHK credentials, but modern Android
phones usually do not allow direct access to app-private data. A rooted device
or rooted Android emulator is required.

The current MyNice Android package name is:

```text
com.niceforyou.welcome
```

One confirmed workaround is to use LDPlayer with root mode enabled:

1. Install **MyNice** in the rooted emulator.
2. Sign in and confirm MyNice can control the BiDi-WiFi.
3. Pull the app-private data directory with `adb`:

   ```powershell
   .\adb.exe -s emulator-5554 root
   .\adb.exe -s emulator-5554 pull /data/data/com.niceforyou.welcome/ C:\BidiNice
   ```

4. Open the extracted `databases` directory. The relevant files reported by
   Android users are:

   ```text
   my_nice_general
   my_nice_general-wal
   my_nice_general-shm
   nhk_extra
   nhk_extra-wal
   nhk_extra-shm
   nhk_web
   nhk_web-wal
   nhk_web-shm
   ```

5. Keep each SQLite database together with its `-wal` and `-shm` companion
   files. The app uses SQLite WAL mode, so the main database file alone can be
   incomplete.

6. Query `nhk_extra`. The useful Android table is `nhk_credentials`:

   ```bash
   sqlite3 nhk_extra
   ```

   Then in the SQLite shell:

   ```sql
   PRAGMA wal_checkpoint(TRUNCATE);
   .tables
   SELECT device_id, nhk_username, nhk_password FROM nhk_credentials;
   ```

Use these values in Home Assistant:

- `device_id` -> **BiDi MAC address**
- `nhk_username` -> **NHK username**
- `nhk_password` -> **NHK password hex**
- **Source/controller ID** can be left empty if it is not present in the
  Android database. The integration then uses the NHK username as the source.

The `my_nice_general` database may also contain an `accessory_table` with module
metadata such as the product type. `nhk_web` contains cloud credentials and is
not needed for this local integration.

Do not publish the extracted app data, SQLite databases, WAL files, or extracted
NHK credentials. If the Android app schema changes, open an issue with the
Android version, MyNice version, extraction method, database file names, and
table names, but redact all secrets.

## Setup

In Home Assistant:

1. Go to **Settings -> Devices & services**.
2. Add **Nice**.
3. Enter:
   - Interface IP address
   - Interface MAC address from `target_mac`
   - NHK username from `username`
   - NHK password hex from `password`
   - Source/controller ID from `source_id`
4. Close MyNice/MyNice Pro before pressing submit.

The integration stores these values in Home Assistant's normal config entry
storage. They are entered by the user during setup and are not hard-coded in the
integration.

## Setup Troubleshooting

- `cannot_connect`: Home Assistant could not validate the BiDi-WiFi local
  service. Check the IP address, VLAN/firewall rules, configured TCP port, and
  that MyNice/MyNice Pro is closed. The integration writes a sanitized warning
  to Home Assistant logs with the exact exception seen during setup.
- `invalid_auth`: One of the extracted credential fields does not match the
  BiDi-WiFi. Re-run the extractor and use `--mac` if multiple devices are
  stored in MyNice.
- TLS EOF or temporary connection errors: close MyNice/MyNice Pro, wait a few
  seconds, then retry. The integration reconnects automatically after transient
  drops once configured.

For deeper setup diagnostics, temporarily enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.nice_bidiwifi: debug
```

Debug logs include connection stage, request type, response type/id, response
size, and T4 payload count. They do not include extracted usernames, source IDs,
target MAC addresses, or passwords.

## Behavior

The integration keeps a persistent local NHK/TLS connection. If the BiDi rejects
the session or MyNice temporarily occupies the connection, the integration closes
the socket, marks the cover unavailable, and retries later instead of hammering
the device.

The cover exposes Home Assistant's position support. For intermediate targets,
the integration sends `open` or `close`, polls the best available position, and
sends `stop` after the position reaches or crosses the requested percentage.
On the validated BiDi-WiFi/NewRobus path this uses real encoder-derived DMP
position. On CU_WIFI devices that only expose live T4 percentages, the same UI
uses the coarse live value, then keeps a cached or simulated display position
between sparse updates. This is intentionally coarse and should not be treated
as millimeter precision.

The cover attributes separate the raw source from the user-facing display:

- `real_position` is the latest real percentage from the device, if available.
- `display_position` is what Home Assistant shows in the cover and the
  `Gate position` sensor.
- `display_position_estimated` is `true` when the displayed percentage is
  currently simulated or held from the last known value.

Home Assistant covers do not have a separate visual state for "stopped
mid-travel". A stopped half-open gate normally appears as open with a
percentage, for example `Open - 18%`, while both open and close actions remain
available.

Calibration is not required. If you only want to open, stop, and close the gate,
do not calibrate; normal open/close control works without it. Calibration only
helps if you want Home Assistant's position slider or set-position service to
land closer to intermediate positions such as 20%, 40%, 60%, or 80%.

The calibration button is a hidden-by-default diagnostic button. Unhide it
only when the gate is visible, the path is clear, and you are ready for the gate
to move repeatedly for several minutes.

When you press the calibration button, the integration first checks whether the
device exposes raw encoder position data.

With encoder data available, the integration:

1. Moves fully closed first so the first opening test starts from a known
   physical position.
2. From closed, calibrates opening targets at 20%, 40%, 60%, and 80%.
3. For each target, returns to the known endpoint, moves toward the target,
   sends `stop` near the learned stop threshold, waits for the gate to settle,
   records the raw encoder value, final percentage, error, command latency, and
   movement timing, then repeats until it has made five attempts.
4. Moves fully open so closing tests also start from a known physical position.
5. From open, repeats the same five-attempt process for closing targets at 80%,
   60%, 40%, and 20%.
6. If the full calibration sequence completes, finishes by closing the gate. If
   calibration fails, it leaves the gate where it stopped so it does not
   surprise someone by closing after an external interruption.

The reason for this sequence is that gates do not stop instantly. The final
position depends on direction, speed, controller latency, inertia, and the raw
encoder value at the moment `stop` is sent. Calibration learns a
direction-specific stop table from real motion instead of assuming that sending
`stop` exactly at 40% will settle at 40%.

For each target, the stored stop threshold comes from the best stable evidence:
two consecutive attempts within 2% when available, otherwise the best
non-outlier attempt. If the gate is still moving after the settle timeout,
calibration sends another `stop` command and records that try as invalid instead
of learning from a moving position. Later intermediate position requests use the
calibrated table and interpolate between neighboring points when possible.

Without encoder data, the integration uses a lower-confidence time-based
calibration:

1. Moves fully closed first.
2. Measures three full openings from closed to open.
3. Measures three full closings from open to closed.
4. Stores every timing sample and selects the median duration for each
   direction.
5. Stores direction-specific full-travel durations and speeds.

This time-based profile improves display animation and can provide approximate
set-position timing by moving for the calculated duration and sending `stop`.
It does not learn precise per-target stop corrections because the integration
cannot verify the final raw position after each stop.

During calibration it polls every 0.5 seconds and waits 0.5 seconds after a
stop or fully reached endpoint before sending the next movement command.

Calibration writes detailed Home Assistant log lines with the prefix
`Nice calibration:`. It also exposes a hidden-by-default diagnostic
sensor named `Position calibration report` with recorder-safe summary
attributes. When calibration finishes or fails, the full detailed report is
written to Home Assistant logs in chunks with the prefix
`Nice calibration report`. The full report includes a quality grade,
max/average error, failed points, all attempts per target, command latency,
movement duration, and the event log.

For the validated BiDi-WiFi/NewRobus encoder path, position is calculated as:

```text
(04/11 - 04/19) / (04/18 - 04/19) * 100
```

Where:

- `04/11` = current encoder position
- `04/18` = open/max encoder value
- `04/19` = closed/min encoder value

State values:

- `04/01 = 01 ff 00 00` -> stopped
- `04/01 = 02 ff 00 00` -> opening
- `04/01 = 03 ff 00 00` -> closing
- `04/01 = 04 ff 00 00` -> open
- `04/01 = 05 ff 00 00` -> closed

For CU_WIFI devices that do not answer the normal DMP status reads, the beta
fallback can also use live T4 events:

- NHK `DoorStatus` from live `STATUS` / `CHANGE` frames for movement state.
- T4 `04/40` frames for coarse percentage. If one reports `stopped` while
  NHK `DoorStatus` still says the gate is moving and the percentage is not near
  an endpoint, the integration keeps the movement state and only uses the
  position.
- T4 `04/02` frames for movement or endpoint state.

Those CU_WIFI values are expected to be less smooth than the real encoder path.

## Dashboard Slider

The integration exposes Home Assistant's native cover position feature. To show
a horizontal slider in a dashboard, use a Tile card with the cover position
feature:

```yaml
type: tile
entity: cover.your_gate_entity
features:
  - type: cover-position
  - type: cover-open-close
```

The exact layout of Home Assistant's built-in cover detail dialog is controlled
by the Home Assistant frontend, not by this integration.

## Helper Entities

Home Assistant tracks visibility and enabled state separately:

- Visible entities appear normally when the integration is first added.
- Hidden entities are created and updated, but Home Assistant hides them from
  default views until you unhide them.
- Disabled entities are not created until manually enabled.

Existing Home Assistant entity registry settings are preserved across upgrades,
so entities created by older versions may keep their previous hidden or disabled
state. For a new installation, the defaults are split by expected use.

Visible by default:

- `cover`: open, close, stop, current position, and set-position slider with
  optional calibration.
- `switch`: open/close gate toggle.
- `button`: partial open 1, partial open 2, partial open 3, and step-step.
- `switch`: auto close, photo close, and always close settings.
- `number`: pause time, photo close time, always close time, opening/closing
  force, opening/closing speed, and partial-open position settings.
- `sensor`: connection state.
- `sensor`: gate position.

Hidden by default:

- Optional action buttons such as courtesy light, lock, unlock, refresh status,
  and reconnect.
- Advanced BusT4 settings such as standby, pre-flash, key lock, maintenance
  threshold, raw mode bytes, and read-only setting mirrors.
- Diagnostic/support sensors such as last update, last error, reconnect count,
  encoder values, calibration state, firmware, hardware, and serial metadata.
- Experimental/raw entities such as the decoded `04/D1` bits, raw diagnostic
  bytes, input flags, debug command sensors, and optional OXI metadata.

Some hidden entities are disabled by default when they are raw, experimental,
verbose, or safety-sensitive enough that they should be enabled deliberately
from the entity registry.

See [entity_reference.md](entity_reference.md) for the full entity list,
purposes, visibility defaults, and enabled defaults.

## Future Work

The integration currently exposes the entities I need for daily use, but the
BiDi-WiFi can do more. Additional MyNice Pro controls can be added by following
the same process used for the existing DEP actions: capture one app action,
decrypt or inspect the local NHK/T4 frame, document the observed behavior, and
add the smallest safe Home Assistant entity for that capability.

The following MyNice Pro controls are good candidates if a contributor wants to
spend the time capturing and validating packets:

- High-priority and condominium step-step variants.
- Open-and-block, close-and-block, unblock-and-open, and unblock-and-close.
- Master/slave open/close/step-step, where applicable.
- BlueBUS search, redo position search, reset, and configuration writes.

They should be added only after their local command names or T4/DMP frames are
confirmed and tested safely. To map a new MyNice Pro control, follow the
capability-capture workflow in [CONTRIBUTING.md](CONTRIBUTING.md).

## Safety

This controls a physical gate. Test while the gate is visible and keep the
original Nice remote/app available.

Do not publish app-data backups, SQLite databases, pcaps, or extracted passwords.
