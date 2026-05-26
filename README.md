# Nice BiDi-WiFi for Home Assistant

[![HACS Validation](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_nice/actions/workflows/hassfest.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Jordi-14/homeassistant_nice)](https://github.com/Jordi-14/homeassistant_nice/releases/latest)

Custom Home Assistant integration for local control of a Nice gate through a
Nice BiDi-WiFi interface.

This integration talks directly to the BiDi-WiFi local NHK/T4 service over
TLS/TCP 443 and creates one `cover` entity plus helper diagnostic entities.

Latest release: `v0.4.1`

## Features

- Open, stop, and close using the local `DoorAction` service.
- Native Home Assistant cover position support.
- Live position percentage while the gate moves.
- Coarse set-position support by moving in the required direction and sending
  stop once the target percentage is reached or crossed.
- Optional position calibration that moves through 20/40/60/80% targets, learns
  direction-specific stop correction, and interpolates between calibrated points.
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

This integration depends on the BiDi-WiFi local NHK/T4/DMP protocol, which is
not publicly documented by Nice. If you rely on this integration, we recommend
not updating the BiDi-WiFi firmware beyond `2.6.4` unless you are prepared to
retest local control and recover using the official Nice app or remote. A
firmware update could change authentication, command framing, register layout,
or local TCP 443 behavior and break the integration.

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

Download it through HACS, restart Home Assistant, then add **Nice BiDi-WiFi**
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

### Android

The Android extraction flow has not been tested yet. It is documented here so
Android users know what data is needed and can report whether the same database
layout is present.

The current MyNice Android package name is:

```text
com.niceforyou.welcome
```

1. Install **MyNice** from Google Play and configure the BiDi-WiFi normally.
2. Confirm the Android app can control the gate.
3. Export the MyNice app data using a method that can read private app data,
   such as a rooted device, a full-device backup tool, or another Android app
   data extraction workflow. Modern non-rooted Android devices may block this;
   `adb backup` often does not work for current apps.
4. Search the extracted app data for:

   ```text
   CachedData.sqlite
   ```

   On a rooted device, it may be under the app-private data tree for
   `com.niceforyou.welcome`.
5. Run the same extractor against that SQLite file:

   ```bash
   python3 scripts/extract_mynice_credentials.py \
     "path/to/CachedData.sqlite"
   ```

6. If there is more than one BiDi-WiFi stored in MyNice, pass the BiDi MAC
   address:

   ```bash
   python3 scripts/extract_mynice_credentials.py \
     "path/to/CachedData.sqlite" \
     --mac "AA:BB:CC:DD:EE:FF"
   ```

If the extractor says `No credential row found in ZACCESSORYCREDENTIALENTITY`,
the Android app may store the credentials differently. In that case, do not
publish the database; open an issue describing the extraction method, Android
version, MyNice version, and whether a `CachedData.sqlite` file was present.

## Setup

In Home Assistant:

1. Go to **Settings -> Devices & services**.
2. Add **Nice BiDi-WiFi**.
3. Enter:
   - BiDi IP address
   - BiDi MAC address from `target_mac`
   - NHK username from `username`
   - NHK password hex from `password`
   - Source/controller ID from `source_id`
4. Close MyNice/MyNice Pro before pressing submit.

The integration stores these values in Home Assistant's normal config entry
storage. They are entered by the user during setup and are not hard-coded in the
integration.

## Setup Troubleshooting

- `cannot_connect`: Home Assistant cannot reach the BiDi-WiFi local service.
  Check the IP address, VLAN/firewall rules, and TCP 443 reachability.
- `invalid_auth`: One of the extracted credential fields does not match the
  BiDi-WiFi. Re-run the extractor and use `--mac` if multiple devices are
  stored in MyNice.
- TLS EOF or temporary connection errors: close MyNice/MyNice Pro, wait a few
  seconds, then retry. The integration reconnects automatically after transient
  drops once configured.

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
`Nice BiDi-WiFi calibration:`. It also exposes a disabled-by-default diagnostic
sensor named `Position calibration report` with recorder-safe summary
attributes. When calibration finishes or fails, the full detailed report is
written to Home Assistant logs in chunks with the prefix
`Nice BiDi-WiFi calibration report`. The full report includes a quality grade,
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
- Current, closed, and open encoder positions.
- BiDi-WiFi firmware, hardware, and serial.
- Control-unit firmware, hardware, serial, and product detail.

## Future Work

The following controls appeared in MyNice Pro but need more investigation before
they should be exposed as Home Assistant entities:

- Partial open 1/2/3.
- Step-step.
- Courtesy light on/off.
- Master/slave open/close, where applicable.

They will be added only after their local command names or T4/DMP frames are
confirmed and tested safely.

## Safety

This controls a physical gate. Test while the gate is visible and keep the
original Nice remote/app available.

Do not publish app-data backups, SQLite databases, pcaps, or extracted passwords.
