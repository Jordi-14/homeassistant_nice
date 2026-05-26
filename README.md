# Nice BiDi-WiFi for Home Assistant

Custom Home Assistant integration for local control of a Nice gate through a
Nice BiDi-WiFi interface.

This integration talks directly to the BiDi-WiFi local NHK/T4 service over
TLS/TCP 443 and creates one `cover` entity.

## Current Scope

- Open, stop, and close using the local `DoorAction` service.
- Real state from DMP register `04/01`.
- Real position from DMP registers `04/11`, `04/18`, and `04/19`.
- Faster polling while the gate is moving, slower polling while idle.
- Automatic reconnect after BiDi reboot, HA restart, and transient TLS EOFs.

## Requirements

- The BiDi-WiFi must be reachable from Home Assistant on TCP 443.
- The BiDi-WiFi should keep normal WAN/cloud access enabled.
- MyNice/MyNice Pro should be closed while Home Assistant is using local control.
- Network ACLs must allow Home Assistant to reach the BiDi IP on TCP 443.

If Home Assistant and the BiDi-WiFi are on different VLANs, the firewall must
allow Home Assistant to initiate TCP 443 connections to the BiDi-WiFi. No local
IP address, MAC address, username, or password should be committed to this
repository.

## Installation

Copy this folder into your Home Assistant config directory:

```text
custom_components/nice_bidiwifi
```

Then restart Home Assistant.

For HACS as a custom repository, add this repository as an integration repo.

## Extracting Credentials

The integration needs the local credential stored by the normal MyNice app.
Use iMazing to make a MyNice app-data backup, extract it, then locate:

```text
Container/Library/Application Support/CachedData.sqlite
```

Run:

```bash
python3 scripts/extract_mynice_credentials.py \
  "path/to/Container/Library/Application Support/CachedData.sqlite"
```

It prints:

- `target_mac`
- `username`
- `password`
- `source_id`

Treat the password as a gate-control secret.

## Setup

In Home Assistant:

1. Go to **Settings -> Devices & services**.
2. Add **Nice BiDi-WiFi**.
3. Enter:
   - BiDi IP address
   - BiDi MAC address
   - NHK username
   - NHK password hex
   - Source/controller ID
4. Close MyNice/MyNice Pro before pressing submit.

The integration stores these values in Home Assistant's normal config entry
storage. They are entered by the user during setup and are not hard-coded in the
integration.

## Behavior

The integration keeps a persistent local NHK/TLS connection. If the BiDi rejects
the session or MyNice temporarily occupies the connection, the integration closes
the socket, marks the cover unavailable, and retries later instead of hammering
the device.

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

## Safety

This controls a physical gate. Test while the gate is visible and keep the
original Nice remote/app available.

Do not publish app-data backups, SQLite databases, pcaps, or extracted passwords.
