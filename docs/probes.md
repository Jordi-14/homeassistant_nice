# Diagnostic Probes

The repository includes local read-only scripts for collecting capability and
status reports. Use these after provisioning the BiDi-WiFi and extracting
credentials.

Run the scripts from the root of a checkout of this repository. They load the
standalone protocol client directly and do not require Home Assistant or
`homeassistant-stubs`. A virtual environment is only needed for repository
development and tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_test.txt
```

Installing `requirements_test.txt` provides the dependency set used by the
repository checks, but is not required for the probes themselves.

## Capability Probe

The capability probe dumps the BiDi-WiFi `INFO` service tree plus the status
registers currently used by the integration.

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

## CU_WIFI Status Probe

For CU_WIFI / Robus devices where commands work but DMP status returns
`Code 14`, use the live read-only probe:

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
the report.

The probe authenticates locally, keeps one session open, listens for async
frames, and polls read-only `STATUS`, `T4_STATUS`, and `INFO`. It does not send
`CHANGE`, `DEP`, open, stop, close, or partial-open commands.

After the live capture it also records the current integration DMP status path
and read-shaped NHK selector probes. With `--exhaustive`, it also runs the
broadest read-only post-live scan currently known: controller, OXI/radio,
status, position, diagnostics, and `GET` selector candidates. Do not use
`--include-sensitive` for reports shared publicly.

An exhaustive scan can take a long time on controllers that answer slowly. The
probe checkpoints the partial JSON every 50 DMP reads when `--output` is set.
To continue at a known read number, reuse the same options with a new output
file and add, for example, `--dmp-start-index 401`. Use
`--checkpoint-every 0` to disable checkpoints or another positive value to
change their frequency. The focused profile remains the preferred first pass;
use `--exhaustive` only when the focused report has not found the needed data.

This probe is useful when a CU_WIFI beta mostly works but state, position, or
obstruction behavior does not match the physical gate. It captures the live
NHK/T4 frames the integration relies on for the CU_WIFI fallback path.

## Register Map Probe

Use this probe to add a controller model to
[register_map.md](register_map.md): it shows which registers the controller has
and in which format. It is most useful for models not in the map yet, for
example swing gates with two motors.

```bash
python3 scripts/probe_register_map.py \
  --host <bidi_ip> \
  --credentials credentials.json \
  --output register_probe.json
```

What it reads (DMP read requests only, flags `0x89` INFO and `0x99` GET):

- INFO of all 256 group 04 parameters of the control unit (`00.03`), then GET of
  each parameter whose INFO is not `FD` (not supported);
- the STD identity (`00/04`, `00/08`-`00/0C`) of the control unit and, unless
  `--no-oxi` is used, of an OXI receiver (`00.0A`), plus the OXI receiver type
  (`0A/00`) and remote coding (`0A/41`).

What it never sends or reads:

- no `SET`, `DEP`, `CHANGE`, open, stop or close: the gate does not move;
- no GET of the registers that start a procedure or a move in MyNice Pro
  (search, delete, reset, activate, go to position, test, calibration; see
  group F of the register map). Only their INFO is read;
- no MAC address, password, Wi-Fi setup code or product detail (serial-like),
  and no OXI PIN, keys, certificates or remote codes.

Before the run:

1. In Home Assistant, disable the integration entry (Settings → Devices &
   services → Nice BiDi-WiFi → ⋮ → Disable), so only one client talks to the
   interface. Enable it again after the run.
2. Make sure nobody uses the gate during the run. It takes a few minutes.

The JSON report has one entry per register with the INFO and GET status (`OK`,
`F0`, `F1`, `F9`, `FD`) and the raw data bytes. It contains the controller
product and firmware, but no host, MAC, username or serial number. Group 04
values are controller settings and are safe to share; check the file before you
post it and remove anything you do not want to publish. Attach it to an issue
with the controller model and the gate type (sliding, swing with one or two
motors, barrier, ...).

## Sharing Reports

Use the default redacted output for public issues. Do not share:

- `credentials.json`;
- unredacted probe JSON;
- app-data backups;
- SQLite databases or WAL files;
- packet captures;
- NHK usernames, passwords, source IDs, MAC addresses, serial numbers, local IPs,
  or account identifiers.

For public reports, include the integration version, interface product, firmware
versions if visible, physical gate state during each capture, and the redacted
JSON report.
