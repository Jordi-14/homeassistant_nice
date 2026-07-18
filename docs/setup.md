# Setup and Credential Extraction

This guide covers provisioning the BiDi-WiFi, extracting the local NHK
credentials, and adding the integration to Home Assistant.

## Configure the BiDi-WiFi

This integration expects the BiDi-WiFi to be configured with the normal
**MyNice** app and connected to the same network that Home Assistant can reach.
The installer-focused **MyNice Pro** app is useful for diagnostics and reverse
engineering, but the normal Home Assistant setup uses the local credential
stored by **MyNice**.

1. Install and power the BiDi-WiFi normally.
2. Reset or provision the BiDi-WiFi if it is not already joined to your home
   network.
3. Connect the iPhone to the Wi-Fi network you want the BiDi-WiFi to join.
   MyNice provisions the BiDi-WiFi onto the network the iPhone is currently
   using.
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
8. Put the iPhone back on the normal Wi-Fi and confirm that MyNice can still
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

The integration needs the local NHK credential stored by the normal MyNice app.
Treat the password as a gate-control secret.

Home Assistant diagnostics redact the configured host, MAC address, username,
password, source/controller ID, serial numbers, and raw register details before
export.

## iPhone

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

## Android

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
   SELECT device_id, nhk_username, nhk_password, nhk_controller_id
     FROM nhk_credentials;
   ```

Use these values in Home Assistant:

- `device_id` -> **BiDi MAC address**
- `nhk_username` -> **NHK username**
- `nhk_password` -> **NHK password hex**
- `nhk_controller_id` -> **Source/controller ID**

For normal MyNice credentials and the final Home Assistant integration setup,
do not leave the source/controller ID empty when `nhk_controller_id` is present.
Several Android extractions need that exact value for authentication. The client
does have a source fallback for MyNice Pro simulation/research tooling, but that
fallback should not be treated as the recommended Home Assistant setup path.

The `my_nice_general` database may also contain an `accessory_table` with module
metadata such as the product type. `nhk_web` contains cloud credentials and is
not needed for this local integration.

There is currently no confirmed non-root Android local extraction path. Modern
Android app-private storage generally blocks normal ADB backup, file browsing,
and `run-as` for this app. A rooted emulator such as LDPlayer remains the known
local workaround. If you cannot or do not want to extract local credentials, use
one of the cloud-capable beta builds instead of sharing private app data.

Do not publish the extracted app data, SQLite databases, WAL files, or extracted
NHK credentials. If the Android app schema changes, open an issue with the
Android version, MyNice version, extraction method, database file names, and
table names, but redact all secrets.

## Add the Integration in Home Assistant

1. Go to **Settings -> Devices & services**.
2. Add **Nice**.
3. Enter:
   - Interface IP address
   - Interface MAC address from `target_mac`
   - NHK username from `username`
   - NHK password hex from `password`
   - Source/controller ID from `source_id` on iOS exports or
     `nhk_controller_id` on Android extractions
4. Close MyNice/MyNice Pro before pressing submit.

The integration stores these values in Home Assistant's normal config entry
storage. They are entered by the user during setup and are not hard-coded in the
integration.

## Troubleshooting Setup

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
