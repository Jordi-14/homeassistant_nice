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

Latest release: `v0.6.0`

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
- Optional position calibration that moves through 20/40/60/80% targets, learns
  direction-specific stop correction, and interpolates between calibrated points.
- Calibration measures one full opening and one full closing at the start and
  uses 80% of the measured direction speed for display animation. Without
  calibration, display animation falls back to 1% per second.
- Detailed calibration quality report with per-target attempts, final errors,
  corrected stop thresholds, command latency, movement timing, and event logs.
- Real state from DMP register `04/01`.
- Real position from DMP registers `04/11`, `04/18`, and `04/19`.
- Faster polling while the gate is moving, slower polling while idle.
- Automatic reconnect after BiDi reboot, HA restart, and transient TLS EOFs.
- Diagnostic sensors for connection state, last update/error, reconnect count,
  command latency, encoder calibration values, and device firmware/serial data.
- Diagnostic buttons to refresh status immediately or force a local reconnect.

## Compatibility

Known working setup:

- BiDi-WiFi firmware: `2.6.4`
- BiDi-WiFi hardware: `SB725A1-R0-R01`
- Tested control unit family: Nice Robus sliding gate controller family
- Tested control unit firmware: `FG01h`
- Home Assistant: `2024.11.0` or newer

This integration was originally tested with BiDi-WiFi devices and depends on
the local NHK/T4/DMP protocol surface, which is not publicly documented by
Nice. Some devices reporting `interface_product: CU_WIFI` may also work in
basic command-only mode when they expose the same local NHK/T4 command surface.
Full status and position support depends on the local services and DMP
registers exposed by the device.

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
3. Export or extract the MyNice app-data backup to a folder.
4. Locate this SQLite file inside the extracted app container:

```text
Container/Library/Application Support/CachedData.sqlite
```

5. Run the extractor from this repository:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/Container/Library/Application Support/CachedData.sqlite"
```

If you have more than one BiDi-WiFi stored in MyNice, pass the BiDi MAC address
to select the right one:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/Container/Library/Application Support/CachedData.sqlite" \
  --mac "AA:BB:CC:DD:EE:FF"
```

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
  "path/to/Container/Library/Application Support/CachedData.sqlite" \
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
the integration sends `open` or `close`, polls the real encoder-derived
position, and sends `stop` after the position reaches or crosses the requested
percentage. This is intentionally coarse and should not be treated as millimeter
precision.

Calibration is not required. If you only want to open, stop, and close the gate,
do not calibrate; normal open/close control works without it. Calibration only
helps if you want Home Assistant's position slider or set-position service to
land closer to intermediate positions such as 20%, 40%, 60%, or 80%.

The calibration button is a disabled-by-default diagnostic button. Enable it
only when the gate is visible, the path is clear, and you are ready for the gate
to move repeatedly for several minutes.

When you press the calibration button, the integration:

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

During calibration it polls every 0.5 seconds and waits 0.5 seconds after a
stop or fully reached endpoint before sending the next movement command.

Calibration writes detailed Home Assistant log lines with the prefix
`Nice calibration:`. It also exposes a disabled-by-default diagnostic
sensor named `Position calibration report` with recorder-safe summary
attributes. When calibration finishes or fails, the full detailed report is
written to Home Assistant logs in chunks with the prefix
`Nice calibration report`. The full report includes a quality grade,
max/average error, failed points, all attempts per target, command latency,
movement duration, and the event log.

Position is calculated as:

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

Enabled by default:

- `cover`: open, close, stop, current position, and set-position slider with optional calibration.
- `switch`: open/close gate toggle.
- `button`: partial open 1, partial open 2, partial open 3, step-step, courtesy light, courtesy light timer, lock, and unlock.
- `sensor`: connection state.
- `sensor`: position calibration state.
- `sensor`: position calibration quality.
- `button`: refresh status.
- `button`: reconnect.

Disabled by default, but available from the entity registry:

- Position calibration button.
- Position calibration report.
- Last position calibration.
- Position calibration error.
- Last successful update.
- Last error.
- Reconnect count.
- Last command.
- Last command latency.
- Gate position percentage from the latest real BiDi-WiFi status update.
- Current, closed, and open encoder positions.
- BiDi-WiFi firmware, hardware, and serial.
- Control-unit firmware, hardware, serial, and product detail.

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
