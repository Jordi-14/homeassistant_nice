# Beta Testing

Beta releases are intended for deliberate testing before changes reach the
stable release. They may contain protocol, setup, entity, or migration behavior
that has not yet been exercised across every Nice controller family.

Test a beta only when the controlled gate, door, blind, or other equipment is
visible and the original Nice remote or app is available.

## Install a beta

Every GitHub prerelease includes a `nice_bidiwifi.zip` HACS-compatible asset.

### Through HACS

Nice is part of the HACS default repository list, so no custom repository URL
is required. Open the Nice repository in HACS, enable prerelease/beta updates,
and use the repository menu to redownload or select the desired version. The
exact labels can vary between HACS versions. Restart Home Assistant after the
download and confirm the integration version in the device or issue
diagnostics. Disable prerelease updates again if the installation should return
to the stable release channel after testing.

### Manually

1. Download `nice_bidiwifi.zip` from the selected
   [GitHub prerelease](https://github.com/Jordi-14/homeassistant_nice/releases).
2. Create a full Home Assistant backup. Backing up
   `custom_components/nice_bidiwifi` alone does not preserve config-entry
   migrations.
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
- One-time MyNice import asks only for the normal account login, creates entries
  with the returned per-device NHK credentials, and does not retain the account
  password or access token.
- Recommended local plus cloud fallback normally uses LAN, changes to cloud
  only after bounded failures, and returns to LAN after stable recovery.
- Active-route, local-route, and cloud-route sensors reflect actual behavior.
- Cloud diagnostics report TLS encryption enabled and relay certificate and
  hostname verification disabled. This is required by the current Nice relay;
  test cloud modes only if you accept the documented impersonation risk.
- Persistent events improve updates without breaking polling after reconnect.
- A live `04/40` position event never changes an actively opening or closing
  gate to `open` or `closed` before a trusted terminal-state update arrives.
- Starting another command or set-position operation during background polling
  does not make the device or its entities flash unavailable.
- Existing BusT4 settings retain their values and moving-state safety checks.
- Opening and closing speed settings remain unavailable on ARIA200/ARIA200S or
  CLBOX controllers, whose speed-register encoding is not verified.
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

The preferred rollback is to restore the full Home Assistant backup created
immediately before installing the beta. This restores both the integration
files and its config-entry schema.

Changing only the integration version in HACS, restoring only
`custom_components/nice_bidiwifi`, or restoring only a partial file backup
cannot reverse a config-entry migration. For example, the `0.8.0` refactor
migrates Nice entries from schema version 1 to version 2. A `0.7.x` integration
will then reject that entry with a message that its version is higher than the
version supported by the installed integration.

If no pre-beta Home Assistant backup exists:

1. Record the current entity IDs and the non-secret setup details needed to add
   the device again.
2. Remove the migrated Nice config entry while the beta is installed.
3. Install the older integration version and restart Home Assistant.
4. Add the Nice device again using credentials appropriate for that version.
5. Check entity IDs and any automations that reference them.

Do not edit Home Assistant's config-entry storage manually.
