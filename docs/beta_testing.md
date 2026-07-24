# Beta Testing

Beta releases are intended for deliberate testing before changes reach the
stable release. They may contain protocol, setup, entity, or migration behavior
that has not yet been exercised across every Nice controller family.

Test a beta only when the controlled gate, door, blind, or other equipment is
visible and the original Nice remote or app is available.

## Install a beta

Every GitHub prerelease includes a `nice_bidiwifi.zip` HACS-compatible asset.

### Through HACS

Open the Nice repository in HACS, enable prerelease/beta versions if necessary,
and use the repository menu to redownload or select the desired version. The
exact labels can vary between HACS versions. Restart Home Assistant after the
download and confirm the integration version in the device or issue
diagnostics.

### Manually

1. Download `nice_bidiwifi.zip` from the selected
   [GitHub prerelease](https://github.com/Jordi-14/homeassistant_nice/releases).
2. Back up the existing `custom_components/nice_bidiwifi` directory.
3. Replace its contents with the files from the ZIP.
4. Restart Home Assistant.
5. Confirm the loaded integration version before operating the gate.

Do not mix files from different releases.

## Refactor beta checklist

Existing entries should migrate in place. Device identifiers, entity unique IDs,
the main cover, the open/close switch, and the gate-open binary sensor must
remain stable.

Test the parts relevant to your setup:

- Home Assistant restarts with the existing entry and entities intact.
- Open, stop, close, step-step, and configured partial-open actions still work.
- State and real position agree with the physical installation.
- A state-only controller does not gain fabricated position or set-position.
- Zeroconf discovery updates an existing device address without creating a
  duplicate entry.
- Fully local mode never requires the Nice relay.
- Fully cloud mode works without LAN reachability.
- Recommended local plus cloud fallback normally uses LAN, changes to cloud
  only after bounded failures, and returns to LAN after stable recovery.
- Active-route, local-route, and cloud-route sensors reflect actual behavior.
- Persistent events improve updates without breaking polling after reconnect.
- Existing BusT4 settings retain their values and moving-state safety checks.
- Optional interface logs, access groups, interface naming, clock sync, and
  reboot remain disabled until deliberately enabled.

Do not test force, speed, installer rules, binding, reset, firmware, or other
high-risk writes unless the exact controller behavior and recovery procedure are
already known.

## Report a beta problem

Include:

- beta version;
- Home Assistant version;
- controller product, firmware, and hardware versions;
- selected connection mode;
- whether the failure affects local, cloud, or both routes;
- expected and observed physical behavior;
- whether the official remote/app still works;
- Home Assistant diagnostics captured after reproducing the issue.

Diagnostics include bounded capability, route, event, command, calibration, and
administration information. They redact configured credentials and local
identifiers. Review the file before publishing it and do not attach app
backups, databases, access tokens, credentials, local IP addresses, MAC
addresses, serial numbers, or raw packet captures.

## Roll back

Select the previous stable version in HACS, or restore the backed-up integration
directory for a manual installation, and restart Home Assistant. Config-entry
and entity identity migrations are designed to remain compatible, but keep a
Home Assistant backup before testing a beta that changes setup or storage.
