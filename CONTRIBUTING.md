# Contributing

Thanks for improving Nice for Home Assistant.

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

## Discovery Toolbox

This section collects the reusable discovery techniques that helped build the
integration. It intentionally skips dead ends that are no longer useful, such as
replaying encrypted TLS payloads or rediscovering the NHK signing flow. Start
with read-only probes and packet observation, then move to action captures only
when the gate is visible and the test is deliberate.

### Local Service Reconnaissance

Use this when a device, firmware version, VLAN, or AP/provisioning mode behaves
differently from known setups.

First confirm the BiDi-WiFi can be reached on the expected local service:

```bash
nc -vz <bidi_ip> 443
nmap -Pn -sV <bidi_ip>
```

For TLS details without assuming the service is HTTP:

```bash
openssl s_client -connect <bidi_ip>:443 -tls1_2
```

Useful things to record in sanitized notes:

- TCP 443 reachable or not.
- TLS handshake succeeds or fails.
- TLS version/cipher if visible.
- Whether the certificate identity looks like a local BiDi-WiFi/Nice device.
- Whether MyNice/MyNice Pro was open at the same time.

Do not paste raw certificates, serial numbers, local IP addresses, or MAC
addresses into public issues.

### mDNS and Local Discovery

The apps and the BiDi-WiFi use local discovery. On macOS, these commands are
useful during normal LAN testing and while connected to the temporary
`NiceBIDIWIFIxxxxxx_AP` hotspot:

```bash
dns-sd -B _wnc-config._tcp local
dns-sd -B _nap._tcp local
dns-sd -B _hap._tcp local
```

If a service appears, resolve it with the service name shown by `dns-sd`:

```bash
dns-sd -L "<service_name>" _wnc-config._tcp local
dns-sd -L "<service_name>" _nap._tcp local
```

Useful sanitized findings:

```text
Service: _wnc-config._tcp
Port: 443
TXT keys: model, deviceid, protovers, srcvers
Address path: app device -> local BiDi-WiFi address
```

Redact exact device IDs, serials, MAC addresses, hostnames that include private
identifiers, and local IP addresses before sharing.

### Packet Capture and Inspection

Packet captures are for finding timing, endpoints, discovery, request types, and
repeatable differences. Do not attach raw captures publicly.

For a normal LAN or AP-mode Mac capture:

```bash
sudo tcpdump -i <interface> -nn -e -s 0 -U -w nice-capture.pcap
```

For an iPhone RVI capture, use the RVI workflow below. RVI is usually better
than Mac Wi-Fi capture because it sees the iPhone's own traffic.

Useful inspection commands:

```bash
tcpdump -nn -tttt -r nice-capture.pcap 'udp port 5353'
tcpdump -nn -tttt -r nice-capture.pcap 'host <phone_ip> and host <bidi_ip>'
tcpdump -nn -tttt -r nice-capture.pcap 'tcp port 443'
tshark -r nice-capture.pcap -q -z conv,tcp
tshark -r nice-capture.pcap -q -z conv,udp
```

When comparing one action against another, note:

- capture file name;
- exact app screen/action;
- physical gate state before and after;
- first packet time of the user action;
- local endpoint and port;
- TCP/TLS record lengths or response sizes;
- whether the difference repeats across two captures of the same action.

Good public notes look like this:

```text
Capture: partial-open-1
Start state: closed
App action: Partial open 1 button
Observed behavior: gate moved to pedestrian opening
Local endpoint: app -> BiDi-WiFi TCP 443
Repeatable difference: one client TLS record appears immediately after tap
Plaintext available: no
```

If a capture file is truncated or a tool reports a broken packet, do not throw
it away immediately. `tcpdump` and `tshark` may still read earlier packets well
enough to recover endpoints, timing, and mDNS data.

### Capture Matrix for New Features

Use a small matrix instead of one long mixed capture. The goal is to make each
diff obvious.

For a read-only state or sensor:

| Capture | What to do |
| --- | --- |
| baseline | Open the app screen but do not change anything. |
| closed | Put the gate fully closed, then open the relevant app screen. |
| opening | Start opening, then open or refresh the relevant app screen. |
| open | Put the gate fully open, then open the relevant app screen. |
| closing | Start closing, then open or refresh the relevant app screen. |
| stopped halfway | Stop mid-travel, then open or refresh the relevant app screen. |

For analog values such as a temperature, counter, or current-position display,
try to collect at least two or three different visible values. A candidate
register is much stronger when it changes with the app value and stays stable
when the app value is stable.

For commands:

| Capture | What to do |
| --- | --- |
| baseline | Open MyNice Pro and do not press a control. |
| action once | Press exactly one control once. |
| action repeat | Return to a known state and press the same control again. |
| opposite action | Capture the paired action, such as close after open. |

For configuration settings:

| Capture | What to do |
| --- | --- |
| read current | Open the settings screen and record the visible value. |
| change away | Change one value only. |
| change back | Restore the original value. |

Do not test force, speed, BlueBUS search, reset, position-search, or other
installer-level writes unless you already know how to recover the installation.

### App Data and App Resource Inspection

App data is useful for credentials, stored device metadata, and sometimes local
configuration names. The normal extraction path is documented in the README and
in the credential step below.

When inspecting app data:

- Keep SQLite databases with their `-wal` and `-shm` files.
- Search for obvious database names and tables before trying raw binary files.
- Prefer sanitized schema/table/column names over raw row contents in issues.
- Never share account identifiers, tokens, passwords, source IDs, serials, MAC
  addresses, local IPs, or Wi-Fi credentials.

Static app package inspection can occasionally reveal UI labels, JSON resources,
or command vocabulary. It is not a primary path for this project because the
important protocol implementation may be inside encrypted app binaries. Use it
only as a lightweight clue source, not as the main discovery workflow.

### Public Protocol Clues

Before proposing a new register or entity, compare findings with public Nice
BusT4 work and community reports. Useful references include:

- Home Assistant Community threads about Nice BiDi-WiFi, CU_WIFI, and BusT4.
- ESPHome Nice BusT4 projects.
- `ngem1/esphome-nice-bidiwifi`.

Use these sources as clues, not as proof. A register name from another
controller becomes useful here only after it is confirmed against a local probe,
an app capture, or a real physical behavior.

When reporting a candidate register, include:

```text
Candidate: 04/11
Source: public BusT4 reference plus local read-only probe
Observed values: closed lower, open higher, moving changes continuously
Mapped meaning: current encoder position
Confidence: high/medium/low
```

For write registers, include the read value before and after the app changes the
setting. Do not add writable Home Assistant entities from public references
alone.

### Hardware BusT4 Fallback

Hardware BusT4 capture/control is a last resort for cases where the official
apps expose useful values but the BiDi-WiFi local NHK/T4 surface does not. It is
not needed for normal integration testing.

Only consider this path when:

- the control unit has an accessible BusT4/OView-style connector;
- the tester understands the electrical and safety risks;
- the gate is visible during all testing;
- read-only local probes and app captures have not exposed the value.

Do not wire two BiDi-WiFi modules to the same BusT4 connector just to capture
traffic. Do not guess voltage levels or bus wiring. Treat public ESPHome BusT4
projects as references for protocol clues unless you are deliberately building a
separate hardware integration.

### Paths That Are Usually Not Worth Repeating

These were useful during the original investigation but should not be a normal
contributor workflow now:

- Replaying captured TLS application data or encrypted T4 bodies. The payloads
  are session-specific and are expected to fail outside their original session.
- Rediscovering the NHK signing formula. The integration already implements the
  local NHK session/signing flow.
- Hand-building raw WNC/NAP XML probes or fake-auth challenge sweeps unless a
  maintainer is deliberately changing the low-level client. The repository
  probes and integration client already cover the known useful NHK/T4 paths.
- Broad blind write scans. Use read-only probes first and only write values when
  the register and expected behavior are understood.
- iOS TLS key extraction, jailbreak instrumentation, or TLS MITM as a first
  step. Prefer RVI timing/endpoints, read-only probes, and the controlled proxy
  workflow.
- Deep static reverse engineering of App Store binaries as the first approach.
  App data, app resources, public protocol clues, and live captures have been
  more useful.

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

When using MyNice Pro credentials only for research tooling, captures, or proxy
simulation, the source/controller ID can be omitted if it is not present. The
local client falls back to the NHK username as the source for that workflow. Do
not generalize that to the final Home Assistant setup with normal MyNice
credentials: if `source_id` or `nhk_controller_id` is available, configure it in
Home Assistant.

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

For CU_WIFI / Robus devices where commands work but normal DMP status returns
`Code 14`, run the live read-only status probe:

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
the report. The probe keeps one authenticated session open, listens for async
frames, and polls read-only `STATUS`, `T4_STATUS`, and `INFO`. It does not send
`CHANGE`, `DEP`, open, stop, close, or partial-open commands. After the live
capture it also records the current integration DMP status path, read-shaped NHK
selector probes, and a read-only DMP scan that includes controller, OXI/radio,
status, position, and diagnostics registers.

Use `--exhaustive` when asking a volunteer for one best-effort report. It enables
`GET` selector probes, a broader DMP scan, and longer frame draining after each
request. Do not use `--include-sensitive` for reports shared publicly.

### 3. Observe MyNice Pro Hotspot Traffic

The most useful reverse-engineering workflow so far has been to observe MyNice
Pro while the phone is connected directly to the BiDi-WiFi temporary access
point. This is the same mode the app uses during installer setup.

Put the BiDi-WiFi into its temporary hotspot/setup mode. The network name is
usually similar to:

```text
NiceBIDIWIFIxxxxxx_AP
```

Connect the iPhone running MyNice Pro to that hotspot. When useful, also connect
the Mac to the same hotspot so it can scan the BiDi-WiFi AP-side address and
capture whatever traffic is visible from another Wi-Fi client. In the observed
setup the addresses looked like this:

```text
BiDi-WiFi AP: 192.168.0.1
iPhone:       192.168.0.2
Mac:          192.168.0.3
```

Confirm the Mac has an AP-side address and can reach the BiDi-WiFi:

```bash
ifconfig en0
arp -a
nc -vz 192.168.0.1 443
```

An optional TCP scan can help confirm the local services:

```bash
nmap -Pn -sV 192.168.0.1
```

To capture from the Mac Wi-Fi interface:

```bash
sudo tcpdump -i en0 -nn -e -s 0 -U -w bidi-pro-ap.pcap
```

Important limitation: a Mac that is merely another Wi-Fi client on the BiDi
hotspot may not see unicast traffic between the iPhone and the BiDi-WiFi. An
empty or incomplete Mac Wi-Fi capture does not prove that MyNice Pro is not
talking locally. Treat it as a secondary view.

### 4. Capture From the iPhone With RVI

For iOS testing, the preferred capture path is Apple's Remote Virtual Interface
over USB. RVI captures the iPhone's own IP traffic, so it avoids the Wi-Fi
client visibility limitation above.

Setup:

1. Connect the iPhone to the Mac over USB and trust the Mac.
2. Keep the iPhone connected to the `NiceBIDIWIFIxxxxxx_AP` hotspot.
3. Disable cellular data temporarily when you need a clean local-only test.
4. Start the capture before opening or using MyNice Pro.

Commands:

```bash
xcrun xctrace list devices
rvictl -s <UDID>
ifconfig rvi0
sudo tcpdump -i rvi0 -nn -s 0 -U -w ios-mynicepro-ap.pcap
```

Then use MyNice Pro for one small action or screen at a time. When finished:

```bash
rvictl -x <UDID>
```

RVI can show endpoints, ports, packet sizes, timing, DNS/mDNS discovery, and
whether the app is talking to `192.168.0.1`. If the app uses TLS, RVI will not
show plaintext commands, but it is still useful for proving which local path the
app uses and when.

### 5. Capture One MyNice Pro Action at a Time

For commands or diagnostics that are not visible in `INFO`, capture MyNice Pro
traffic while pressing controls or opening screens in the app.

Use this pattern:

1. Start with a short baseline capture where MyNice Pro opens and no control is
   pressed.
2. Capture one action per file, for example `partial-open-1`,
   `courtesy-light`, or `step-step`.
3. For a diagnostic value, capture one screen/read operation per file, for
   example opening the page that shows current position or motor temperature.
4. For toggles or numbers, capture both read and write behavior: app open with
   the current value, value changed off to on or low to high, and value changed
   back.
5. Record exactly what MyNice Pro showed and what physically happened at the
   gate.
6. Keep captures short and stop the app/proxy between captures so each file has
   a small, obvious diff.

Do not publish raw packet captures or proxy logs if they contain authentication
XML, TLS material, MAC addresses, source IDs, IP addresses, serial numbers,
account identifiers, or app backup paths. A useful PR or issue can include
sanitized notes like:

```text
Action: partial_open_1
Observed behavior: gate moved to pedestrian opening
Protocol: T4_REQUEST / DEP
Plain frame: 55 0c 00 03 50 91 01 05 c6 01 82 05 64 e2 0c
```

### 6. Advanced Plaintext Proxy Workflow

The strongest maintainer workflow found so far is to advertise a controlled
fake BiDi-WiFi endpoint from the Mac, accept MyNice Pro's TLS connection there,
and forward the plaintext NHK request to the real BiDi-WiFi over a separate TLS
connection. That lets the maintainer log the app's plaintext NHK layer and the
real device response while still using the real device for authentication and
session signatures.

Use this only when the gate is visible and the test is deliberate. The proxy
should block setup/pairing writes such as `PAIR` by default. Do not forward
open, close, stop, reset, position-search, force, speed, or installer writes
unless that exact command is being captured and the gate area is safe.

The useful result to contribute is not the raw proxy log. Share a sanitized
mapping instead:

```text
Screen/action: motor temperature page opened
Observed app value: 42 C
Request type: T4_REQUEST / DMP
Candidate register: 04/xx
Response bytes: xx xx xx xx
Notes: value changed with the app display across repeated captures
```

Even with plaintext NHK logs, some inner T4 payloads may still need decoding by
comparing repeated actions, physical behavior, and controller responses.

### 7. Decide the Home Assistant Entity Type

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
