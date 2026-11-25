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

## 0.8.1 compatibility checklist

- On IT4WIFI firmware that rejects the normal handshake, enable **Legacy local
  TLS compatibility** and confirm diagnostics report the option active. Leave it
  disabled on interfaces that connect normally.
- If DMP status returns Code 5, confirm setup succeeds only when INFO advertises
  readable `DoorStatus` and NHK status is actually returned.
- Confirm a DEP action rejected with Code 5 becomes unavailable without hiding
  unrelated action buttons. Do not repeat movement commands just to test this.
- Confirm partial-open 2 and 3 remain available when their corresponding action
  bits are advertised, even if their optional position registers are absent.
- On a position-reporting gate, watch movement between sparse live updates and
  confirm the displayed percentage advances at the observed travel speed without
  large periodic jumps.
- For a delayed first command after idle, capture diagnostics after the delayed
  command and again after an immediate command. Do not change the BusT4 standby
  setting solely as a connection workaround.

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

## Shared-runtime beta (0.9.0b5)

No recalibration or entity recreation is required. This beta refactors the shared runtime; later phases add the remaining app features.

- Compare ordinary opening and closing, a normal Stop, and a reversal with b4. Animation retains the 90% factor and learned directional bands. State-only updates should not re-anchor the percentage or create learning samples.
- Check that newer live position or state survives a slower status poll. Confirmed forward jumps are expected. Continuous movement should not rewind; a stop or reversal can reconcile the estimate.
- Reload the integration and restart HA. Existing names, IDs, calibration and learned speeds should remain present, and the connection should resume without duplicate updates.
- Download integration diagnostics. The feature report separates implementation, device support, permissions and runtime availability. Observations show source and age; runtime diagnostics show pending work and scheduler state.
- Normal user controls take priority over background reads. Expired commands fail rather than being sent later. Unimplemented app features and CORE/ProView access paths remain blocked with explicit reasons.

If reporting a problem, include the loaded version, time and time zone, controller/module firmware, connection mode and sanitized diagnostics. Do not change controller settings or run controller relearning solely to test this refactor.

## Controller configuration beta (0.9.0b6)

- Restart HA and check the integration loads on the first attempt. Core state should become available before the broad diagnostics scan finishes. Check a normal reload too.
- Confirm the six existing on/off setting switches are available while stationary. No toggle is required to establish this. Existing numeric controls, IDs, settings and calibration should remain intact.
- Under disabled entities, optionally enable a parameter sensor and compare its reading, units and timestamp with the app. A setting without verified metadata should explain why it is unavailable. Extra sensors should not add polling until enabled.
- Optionally enable the photo-close/always-close selects or engineering-unit number aliases. Compare their current values without changing controller parameters. The old raw-number entities should remain present.
- If a device reports an indexed value, its elements should remain separate and unnamed as motors until their identities are verified. There should be no fabricated aggregate percentage.
- Confirm ordinary open, Stop, close and partial-open behavior. Position animation retains its directional learned bands and 90% factor. A correction at Stop is permitted.
- Download diagnostics, including when setup has failed. Check the configuration ledger and gap reasons. An unnamed module should no longer produce a text-entity error.

No new calibration, force/safety parameter change, installer procedure or deliberate obstruction test is needed. Additional setters needing the unresolved native menu decoder remain read-only.

## Diagnostic and incident beta (0.9.0b7)

- Check ordinary open, Stop, close and reversal behavior. No deliberate obstruction, parameter change, maintenance reset or controller relearning is required.
- Compare the app's event/history view with the decoded entries in downloaded integration diagnostics. Live and stored equivalents should have the same state/cause/diagnostic names. Module timestamps remain separate from HA receipt time.
- Under disabled entities, optionally enable BlueBus, I/O, hardware, inverter, counter or advanced-history evidence cards. Unsupported/unverified screens should explain their state instead of producing guessed values. Hardware/inverter percentages require the controller's descriptors; M1/M2 stay separate. Disable optional cards afterward if unneeded.
- Check the incident timeline for commands, readings and reported fault codes. A command-arrival event must not identify an external sender, and a limiter level alone must not report a limiter trip. A baseline or retained true Obstruct flag must not create repeated new obstructions.
- All-zero maneuver history can now show unknown with `all_zero_unverified`. This is an uncertainty correction, not proof that a new fault occurred. Advanced DB slots are not assumed to align with DA or use the same enums.
- NHK and DMP counter channels should remain separate. A newly received NHK count must not overwrite both DMP counters.
- Check reload/reconnect, entity IDs and diagnostic export. Failed reads must not make older evidence look freshly captured. The polling-order fix from the entity audit is included, so unread optional parameters get priority over repeat reads.

See [diagnostic contracts and gaps](diagnostic_parity.md) for retention, redaction, decoder provenance and remaining missing-native layouts.

## Phase 5: v0.9.0b8

- Existing entries should load with unchanged identifiers and credentials. Check
  ordinary control, route recovery and movement animation as before. No new
  calibration or controller relearning is required.
- Open Configure, inspect users, and compare the reported module role and counts
  with MyNice. Optionally enable Module permission and disable it again afterward.
- With approved administrator credentials, check that existing configuration
  entities become available when role inspection completes. An unknown or ordinary
  role must not acquire configuration rights.
- On a suitable test installation only, exercise first-user pairing or a new
  administrator-approved access request. Reopen an interrupted flow and confirm
  it resumes approval checks without another PAIR.
- On a suitable test user, check explicit approval/permission confirmation and
  readback. Preserve your established administrator and HA access. Do not attempt
  cloud ownership transfer or physical user deletion through these options.
- Wi-Fi setup remains unavailable; the separate AP probe is read-only. The flow
  must not collect a Wi-Fi password or silently change an existing endpoint.
- Diagnostics should show session state, role and counts without usernames,
  controller identifiers, device passwords, setup codes, SSIDs or keys.

See [Onboarding and module access](onboarding_access.md) for the complete contract.

## Phase 6: v0.9.0b9

- Existing entries and enabled entity selections should stay intact. The two new
  diagnostic sensors start disabled. No installer procedure starts on upgrade.
- Check normal open/Stop/close, position animation, parameters, local connection
  and recovery. No new calibration or controller relearning is needed.
- Open Configure > Installer procedures and device lifecycle. Inspect advertised
  devices and compare their model/hardware/firmware with MyNice. The count is only
  the NHK INFO list; it is not a full BusT4, receiver-memory or BlueBus scan.
- Capture a configuration observation snapshot and download diagnostics. Its
  partial status, capture time, per-value observation time and non-restorable
  status must be clear. No extra parameter reads or writes should occur.
- Check unavailable learning/reset/enrollment/restore/update entries. They should
  explain the missing evidence and return without sending a device request.
- Optional bootloader inspection needs a local, stationary supported interface,
  verified administrator access and a suitable test setup with other apps closed.
  Unsupported read responses remain errors. No controller should enter boot mode,
  stage an image, erase flash or start programming. Reported progress has no assumed
  percent unit. A known RUN observation holds new movement/admin actions in the
  current runtime until a definitive fresh status; Stop remains allowed.
- Restart and interrupted-restart recovery need a suitable visible test device.
  The result proves command acknowledgement, not an independently measured reboot.
  Never repeat an uncertain restart to test whether it might have been delivered.
  Use inspection and explicit recovery acknowledgement instead.
- Check that diagnostics contain no serial numbers from the new inventory, remote
  identifiers/certificates/codes, firmware bytes, vendor signatures or raw frames.

See [Installer workflows and device lifecycle](installer_lifecycle.md). Existing
HA animation calibration does not perform controller position/force programming.

## Phase 7: v0.9.0b10

- Existing device entities, IDs, registry choices, movement learning and calibration
  should remain intact. The optional Nice automation inspection sensor starts
  disabled; nothing logs into the cloud or creates automations on upgrade.
- Review controller automatic closing in Configure > HA and existing Nice
  automations. It must use only existing observations, preserve unknown values and
  show observation time. No extra read or setting change should occur.
- Optionally request cloud inspection with explicit consent and a normal MyNice
  account associated with this configured accessory. Check the scope, source,
  capture time, reported names/status/timer fields and uncertainty against the app.
- Missing or unavailable collections must not appear as zero records. A valid
  empty cloud response is not proof that no local controller/CORE behavior exists.
- Inspect exported diagnostics: names, addresses, exact schedules, account/home/
  automation IDs, credentials and tokens must be absent from the new report.
- A failed or cancelled request must clear account secrets, retain the original
  timestamp of any previous capture and leave local/cloud/fallback policy intact.
- No HA/Nice homes, areas, scenes, rules, schedules, groups, notifications or users
  should be created, changed, imported, synchronized or executed by inspection.
- Known additional device categories must not acquire gate actions through a
  generic advertisement. Verified gates keep their controls; explicit door metadata
  may select the garage cover class without changing its entity ID.

Use native HA for new organization and automation. Review and, if you choose,
disable original Nice behavior in the manufacturer app before adding an equivalent
HA action. CORE runtime verification remains explicitly unavailable.

## Phase 8: v0.9.0b11

- Update/restart with existing entries and registry selections. No re-pairing,
  recalibration, controller relearning or resource import is required. Compare
  ordinary controls, position, configuration values, roles and connection health.
- Repeat optional read-only cloud inspection, especially an account with no Nice
  records. An explicit successful null/list collection may report zero. Missing,
  rejected, malformed or out-of-scope data must stay unknown. A completely unknown
  inspection must no longer claim partial success with no error.
- Share only standard sanitized diagnostics. The new response-shape evidence
  contains fixed field types/presence/result flags, not raw replies, private keys,
  names, schedules, resource IDs or account details. No new credential retention
  or resource mutation is introduced for troubleshooting.
- The explicit CORE reader needs existing CORE credentials and a suitable local
  CORE endpoint with other app sessions closed. It reads INFO and TABLE_READ only,
  compares table versions and exports counts. It must reject a non-CORE endpoint,
  changed/missing versions or unsupported rows before claiming a coherent snapshot.
  It never executes, edits, binds, resets or programs a device. The tool has only
  source/fixture validation so far; actual CORE execution/timezone remains unknown.
- Review `docs/parity_closure.md`, `docs/protocol_gaps.md` and the validation matrix.
  Their dispositions are not claims of universal app or hardware support. Blocked
  commissioning, receiver, firmware and per-motor operations remain blocked.
- Use native HA for organization, automation and notification execution. Keep
  vendor ownership/sharing and management of original Nice resources in the app.

Collect normal-use feedback before a separate release-candidate/stable decision.
No physical safety intervention, unattended movement or installer mutation test is
required to validate the ordinary integration upgrade.

## Corrective beta: v0.9.0b12

Repeat only the optional read-only cloud inspection after updating/restarting.
The cloud-returned identifier is now preserved for REST queries while returned
records are still checked against the normalized accessory scope. No change is
made to NHK credentials, the configured target or connection policy.

An exact SDK RECORD_NOT_FOUND error with explicit empty lists may report no
records. Generic NOT_FOUND without that description, ACCESSORY_NOT_FOUND, denied
access, server failure, missing lists and nonempty contradictory replies remain
unknown/failed. Sanitized diagnostics include only recognized fixed vendor error
identifiers, list lengths and identity-format labels; arbitrary messages, raw
identifiers, credentials and records are not exported. A successful b12 result
still does not confirm CORE execution or the absence of all local controller
behavior.

## Corrective beta: v0.9.0b13

Repeat optional cloud inspection after updating/restarting. The reader now uses
the app's successful macro-user home inventory and does not call the failing
per-accessory scene/rule endpoints. Explicit empty/null home collections may report
zero; omitted or malformed collections remain unknown. Other gateway records are
filtered out and timers must link to scoped rules. No home/resource is imported,
changed or executed, and no account secrets or private records enter diagnostics.
The read-only live result still needs confirmation rather than assuming that a
source-correct parser change has resolved every response variant.
