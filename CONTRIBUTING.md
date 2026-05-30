# Contributing

Thanks for improving Nice BiDi-WiFi for Home Assistant.

## Safety and Privacy

This integration controls a physical gate. Test changes while the gate is
visible and keep the original Nice remote or app available.

Do not commit or attach:

- MyNice or MyNice Pro app-data backups.
- SQLite databases extracted from an iPhone backup.
- Packet captures.
- NHK usernames, passwords, source IDs, MAC addresses, serial numbers, or local
  IP addresses from a private installation.

Use fake values in examples and redact logs before opening issues.

## Adding BiDi-WiFi Capabilities

Use **MyNice Pro** when investigating new gate features. The normal MyNice app
is enough to provision the BiDi-WiFi and extract credentials, but MyNice Pro
usually exposes installer-level actions and settings that are not visible in the
normal app.

This integration is intentionally limited to the entities that have been
captured, understood, and tested. That is not a protocol limit. There are more
MyNice Pro actions, read-only sensors, and configuration settings that should be
straightforward to add once somebody spends the time capturing the packets,
checking what the gate actually does, and contributing the mapping. The process
below is the same process used for the existing extra buttons.

Work from a real gate only when you can see it. Avoid testing locks, force,
speed, reset, BlueBUS search, or position-search commands unless you know how to
recover the installation locally.

### 1. Extract MyNice Pro Credentials

1. Configure the BiDi-WiFi in MyNice Pro and confirm the app can control the
   gate locally.
2. Close MyNice and MyNice Pro before connecting with development tools. The
   BiDi-WiFi can be sensitive to concurrent local sessions.
3. Export the **MyNice Pro** app data from the phone. On iOS, iMazing or another
   app-data backup tool can export the app container.
4. Search the extracted app data for the credential SQLite database. Known iOS
   backups have used names such as:

   ```text
   CachedData.sqlite
   MyNicePro.sqlite
   ```

   A generic search is often fastest:

   ```bash
   find "path/to/extracted/MyNice Pro app data" \( -iname "*.sqlite" -o -iname "*.db" \)
   ```

5. Run the extractor against the database that contains
   `ZACCESSORYCREDENTIALENTITY`:

   ```bash
   python3 scripts/extract_mynice_credentials.py \
     "path/to/MyNicePro.sqlite" \
     > credentials.json
   ```

   If the app contains more than one BiDi-WiFi, select the right one by MAC
   address:

   ```bash
   python3 scripts/extract_mynice_credentials.py \
     "path/to/MyNicePro.sqlite" \
     --mac "AA:BB:CC:DD:EE:FF" \
     > credentials.json
   ```

`credentials.json` is ignored by git. Do not commit it, paste it into issues, or
include it in PRs.

### 2. Dump Advertised Capabilities

Before capturing actions, dump the BiDi-WiFi INFO tree and known status
registers:

```bash
python3 scripts/dump_bidi_capabilities.py \
  --host <bidi_ip> \
  --credentials credentials.json \
  --include-raw-info \
  --output bidi_capabilities.json
```

The capability dump does not move the gate. It authenticates, sends `INFO`, and
reads the same DMP status registers used by the integration. The output redacts
host, MAC address, username, source/controller ID, and serial numbers by
default. The password is never written to the report.

`bidi_capabilities.json` is also ignored by git. If it needs to be shared,
review it first and keep it redacted.

### 3. Capture One MyNice Pro Action at a Time

For commands that are not visible in `INFO`, capture the local NHK/T4 traffic
while pressing controls in MyNice Pro.

Use this pattern:

1. Start with a baseline capture where MyNice Pro opens and no control is
   pressed.
2. Capture one action per file, for example `partial-open-1`,
   `courtesy-light`, or `step-step`.
3. For toggles or numbers, capture both read and write behavior:
   - app open with the current value
   - value changed off -> on, or low -> high
   - value changed back on -> off, or high -> low
4. Record what physically happened at the gate.
5. Stop the app and proxy between captures so each file has a small, obvious
   diff.

Do not publish raw proxy logs if they contain authentication XML, MAC addresses,
source IDs, IP addresses, serial numbers, or app backup paths. A useful PR or
issue can include sanitized notes like:

```text
Action: partial_open_1
Observed behavior: gate moved to pedestrian opening
Protocol: T4_REQUEST / DEP
Plain frame: 55 0c 00 03 50 91 01 05 c6 01 82 05 64 e2 0c
```

### 4. Decide the Home Assistant Entity Type

- Use `button` for one-shot actions such as partial open, step-step, light
  toggle, lock, or unlock.
- Use `sensor` or `binary_sensor` for read-only DMP state.
- Use `switch`, `number`, or `select` only when both the read and write frames
  are understood.
- Keep risky configuration writes disabled by default until more than one gate
  model has been tested.

Every new capability should include tests for frame construction, coordinator
behavior, and entity behavior. Add README or contributing notes when a new
capture process is needed.

## Development Checks

Before opening a pull request, run:

```bash
python3 -m json.tool custom_components/nice_bidiwifi/manifest.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/strings.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/translations/en.json > /dev/null
python3 -m json.tool custom_components/nice_bidiwifi/translations/ca.json > /dev/null
python3 -m pytest tests -q
python3 -m ruff check custom_components tests
python3 -m compileall -q custom_components tests scripts
```

Install test dependencies with:

```bash
python3 -m pip install -r requirements_test.txt
```

When changing UI text, update `strings.json` and all translation files. Logs,
diagnostics internals, and developer-only messages can stay in English.

GitHub Actions run tests, linting, HACS validation, and Hassfest for
repository-level checks.
