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
