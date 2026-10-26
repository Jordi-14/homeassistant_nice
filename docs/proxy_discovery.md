# Capture MyNice Pro Commands

Use this when MyNice Pro can perform a useful action that the integration does
not yet implement. First check that MyNice Pro actually offers the action on
your controller. If it does not, report that result; there is no app command to
capture with this method.

This is the method used during the original BiDi-WiFi investigation: the Mac
advertises a temporary fake BiDi service, MyNice Pro connects to the Mac, and a
Python proxy forwards requests to the real BiDi through a second TLS connection.
The real interface still handles authentication and session signatures.

```text
MyNice Pro on iPhone -> TLS -> Mac proxy -> TLS -> real BiDi -> controller
```

A passive Wi-Fi or RVI packet capture only shows encrypted traffic. This proxy
terminates both TLS connections, so it can inspect the NHK XML. The current
tool also decodes outgoing DEP action frames into the summary using the
integration's existing T4 decoder. It does not replay old requests or generate
new gate commands.

The original successful setup used an iPhone, a Mac, MyNice Pro, and BiDi-WiFi
firmware 2.6.4 in its temporary access-point mode. Other phones, firmware, or
normal LAN discovery may behave differently. The newly packaged tool is based
on that original proxy; it has local tests but needs confirmation on real
hardware. If the app rejects the temporary certificate, stop and report the
failure. Do not install a system-wide CA or change the phone's certificate trust.

## Before starting

- Keep the gate visible. Forwarded controls operate the real gate.
- Use only a known normal action and the specific new action you are studying.
  Avoid lock/block, force, speed, reset, BlueBUS search, position-search, and
  other installer settings. The proxy always blocks `PAIR`, but it is not a
  read-only filter for other commands.
- Disable the HA integration and close MyNice/MyNice Pro on other devices for
  the test. Use one local client at a time.
- Confirm that MyNice Pro already works directly with the real module. If it
  needs authorization, complete the normal direct connection first. Do not
  pair through the proxy or factory-reset/re-provision a working installation.
- Have Python 3.13 or newer, Git, OpenSSL, and macOS `dns-sd` available. The
  proxy uses the standard library and needs no Home Assistant installation.

## 1. Get the tool and make a temporary certificate

On the Mac, open Terminal and run:

```bash
git clone https://github.com/Jordi-14/homeassistant_nice.git
cd homeassistant_nice
python3 --version
python3 scripts/nhk_tls_proxy.py --help
mkdir -p proxy-captures
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -keyout proxy-captures/fake-bidi.key \
  -out proxy-captures/fake-bidi.crt \
  -subj '/CN=AWS IoT Certificate'
chmod 600 proxy-captures/fake-bidi.key
```

If you already have a clean checkout, update it to current `main` instead of
cloning a second copy. The certificate is a temporary lab certificate, not a
copy of your BiDi's certificate. Renew it if testing on a later day.

`proxy-captures/` is ignored by git. Keep its private key and any raw logs local.

## 2. Put the phone and Mac on the BiDi's test network

Use your model's documented method to enable its temporary setup hotspot
without resetting or changing the saved Wi-Fi configuration. If doing this
requires a reset on your model, stop here and report that limitation.

Connect the iPhone and Mac to the `NiceBIDIWIFIxxxxxx_AP` network. Temporarily
disable cellular data on the phone to keep the test local. Check the Mac's Wi-Fi
network details for its address and the interface/router address. In the
original setup, the BiDi was `192.168.0.1` and the Mac was `192.168.0.3`; confirm
your own addresses rather than assuming those values.

In Terminal 1, set these variables to your local values:

```bash
BIDI_IP='192.168.0.1'
MAC_IP='192.168.0.3'
nc -vz "$BIDI_IP" 443
```

Find the BiDi's local service metadata with:

```bash
dns-sd -B _wnc-config._tcp local
```

Note its service name, then press Ctrl-C. Resolve that service using its exact
name from the preceding output:

```bash
dns-sd -L 'REAL-SERVICE-NAME-HERE' _wnc-config._tcp local
```

Record the `model`, `deviceid`, `protovers`, and `srcvers` values locally, then
press Ctrl-C. The advertised device ID is normally the module's MAC address.
Do not post it. This check also confirms you are using the intended module if
you have more than one.

## 3. Start the proxy and advertise it

In Terminal 1, from the repository root, start a baseline capture:

```bash
sudo python3 scripts/nhk_tls_proxy.py \
  --listen-host "$MAC_IP" --listen-port 443 \
  --upstream-host "$BIDI_IP" --upstream-port 443 \
  --cert proxy-captures/fake-bidi.crt \
  --key proxy-captures/fake-bidi.key \
  --label baseline \
  --summary proxy-captures/baseline-summary.jsonl \
  --raw-log proxy-captures/baseline-private.log \
  --acknowledge-gate-control
```

`sudo` is for the local port 443 listener. If it uses a different Python than
your normal terminal, replace `python3` after `sudo` with the full Python 3.13+
path printed by `command -v python3`. Allow incoming connections for this
listener if macOS asks. The listener binds only to the Mac address you supplied.

`--raw-log` is optional. It records private XML for local analysis. Omit that
option if you only want the smaller summary. Existing capture files are refused;
use new names for each run.

Open Terminal 2. Set `BIDI_MAC` and `BIDI_MODEL` to the values you just resolved;
the examples below use placeholders. Match the real `srcvers` and `protovers`
values too if they differ:

```bash
BIDI_MAC='AA:BB:CC:DD:EE:FF'
BIDI_MODEL='Nice Spa - BIDIWIFI - SB725A1'
dns-sd -R NiceDiscoveryProxy_AP _wnc-config._tcp local 443 \
  srcvers=1.20 seed=105 protovers=1.0 \
  "model=$BIDI_MODEL" "deviceid=$BIDI_MAC"
```

Keep that command running. In Terminal 3, repeat the variable assignments and
advertise the second service:

```bash
BIDI_MAC='AA:BB:CC:DD:EE:FF'
BIDI_MODEL='Nice Spa - BIDIWIFI - SB725A1'
dns-sd -R NiceDiscoveryProxy_AP _nap._tcp local 443 \
  flags=1 protovers=1.0 "model=$BIDI_MODEL" "deviceid=$BIDI_MAC"
```

The temporary service uses the real module's identity so the app talks to the
same target through the proxy. Its address resolves to the Mac, while the proxy
connects upstream to the real BiDi. No router, DNS-server, or ARP changes are
required.

## 4. Confirm the app is using the proxy

Open MyNice Pro and use local interface discovery. Select
`NiceDiscoveryProxy_AP`, not the original interface service. In the original
test, both appeared in the app. The proxy terminal should print that both app
and BiDi TLS connections are established, and the summary should grow.

For this first run, open the controller/action screen, wait about ten seconds,
and close MyNice Pro without pressing a control. Stop the proxy with Ctrl-C.
Leave the two advertisements running for the next capture.

If there are no proxy sessions or the summary is empty, the app has not used the
proxy. Do not interpret an action performed through the original service as a
captured command. If authorization fails, pairing is requested, or the app
refuses the temporary endpoint, stop and report the stage that failed.

## 5. Capture a known action and the new action

Repeat the proxy command with `--label normal-open`,
`--summary proxy-captures/normal-open-summary.jsonl`, and
`--raw-log proxy-captures/normal-open-private.log`. Reopen MyNice Pro, select the
proxy, and press normal Open once from fully closed. Record what physically
happens and the time of the press. Close the app and stop the proxy with Ctrl-C.

Return the gate to the same starting state using a normal control. Then repeat
with `--label partial-open`, `partial-open-summary.jsonl`, and
`partial-open-private.log`. Press the pedestrian/one-leaf action once. Record
the exact app label, time, starting state, and physical result.

Repeat that last capture with a new filename and label such as
`partial-open-repeat`. This shows whether the same decoded action occurs again
from the same state. Each run uses a fresh app connection, so encrypted payloads
and signatures may differ even when the decoded command is the same.

Do not force an unadvertised command from Python. The only action forwarded
here is the one you deliberately press in the official app.

## 6. Inspect and share the useful result

Files created under `sudo` are owner-only and may belong to root. After stopping
the proxy, give yourself access to each named report you want to inspect:

```bash
sudo chown "$(id -u):$(id -g)" proxy-captures/baseline-summary.jsonl
```

Repeat with the other exact filenames. Use `less` or a text editor to inspect
each summary. Each line is a JSON record. Useful fields include:

- `label`, `time_utc`, and `session` to line up the button press with a request;
- `direction`, `type`, and `protocol` to distinguish app requests from replies;
- `dep_frames_hex` for outgoing DEP action bytes, decoded with the existing
  integration T4 decoder;
- `blocked` to distinguish a locally blocked request from one sent to the BiDi.

The summary excludes raw XML, NHK signatures, keys, identities, addresses, and
controller names. It does include decoded outgoing DEP bytes; review those and
the label before sharing. If there is no DEP request, that is useful too: the
app may use another request type. Keep the corresponding raw XML private for
further local analysis; it may contain authentication material and identifiers.

Reply in the GitHub issue with the reviewed `*-summary.jsonl` files and a short
note for each run, for example:

```text
App action: exact pedestrian action label
Capture label: partial-open
Starting state: fully closed
Time of press: 18:20:10 local, UTC+1
Result: one leaf opened, other leaf stayed closed
Repeated: same physical behavior on the second run
```

GitHub may not accept `.jsonl` attachments directly. Make a `.txt` copy of the
reviewed summary or paste its relevant lines inside a Markdown code block.
Do not attach private logs, the temporary key, credential JSON, app backups,
or packet captures. A screenshot of the relevant app control is useful after
removing private names and identifiers.

## 7. Restore normal use

Close MyNice Pro, stop the proxy and both `dns-sd` commands with Ctrl-C, and
leave temporary AP mode using the model's normal procedure. Reconnect the phone
and Mac to their normal networks and restore cellular data if you disabled it.
Confirm normal local operation before re-enabling the HA integration. The proxy
does not change the HA entry or install any persistent network rules.
