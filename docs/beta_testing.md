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

## Release candidate: v0.9.0rc1

RC1 uses the same integration code as b13. It remains a prerelease for final
ordinary-use testing, not an automatic stable promotion. Keep existing credentials,
connection mode, entity selections and learned profiles. Restart HA after updating.
No re-pairing or recalibration is required.

Check ordinary controls, position, permissions, restart/reload and recovery with
existing settings. Report the exact loaded integration/Core versions and hardware,
and attach sanitized diagnostics if something differs. Keep prototype/blocked
installer, reset, enrollment, per-motor and firmware procedures blocked; this RC
does not validate their missing contracts. Existing CONNECT Code 1 and other open
hardware-specific investigations remain open until confirmed on a clean baseline.

## Corrective beta: v0.9.0b14

Use b14 instead of RC1 for further testing. The router now forwards the
coordinator's dispatch guard for open/close/stop, DEP and NHK T4Action commands.
This fixes the unexpected `before_dispatch` TypeError introduced in b9 while
retaining permission/lifecycle checks immediately before transmission.

Install through HACS with prereleases enabled and restart HA. Existing credentials,
controller settings and learned profiles remain; no re-pairing, recalibration or
controller relearning is required. Confirm ordinary commands and supported partial
opening during normal use. Do not provoke faults or obstructions.

For a naturally occurring premature stop, attach sanitized native diagnostics
through the GitHub web UI (email attachments may not appear), with the matching
time/timezone. NHK causeCodeX91 now appears in abnormal-stop evidence as a reported
diagnostic with unknown physical cause; later generic stops remain unclassified.
This release does not claim to fix the physical stopping or CONNECT Code 1.

New native evidence: an arm64 Pro library and split APK were supplied on
2026-11-25 and their library bytes match. Exported JNI names cover menu/INFO,
hardware diagnostics, maneuver history and receiver codecs. Native contracts and
exact version association still need audit; no new native binding is enabled in b14.

## Native decoder phase 1: v0.9.0b15

The [three-phase follow-up plan](native_decoder_plan.md) starts with read-only
native decoders. See [audit scope and evidence](native_decoder_audit.md).

Install b15 and restart HA. Optionally enable the disabled diagnostic entity
**Controller automation state**. Its attributes show the reported status,
diagnostic codes and only those optional fields present in the response. It uses
04/01, not the old blocked D3 placeholder. Existing status polls can supply the
snapshot; enabling the entity adds bounded, preemptible reads while stationary.
Compare its information with the app during ordinary use and attach sanitized
integration diagnostics if values differ. Preserve the raw values and the capture
time; optional module masks are numbers, not guessed names for physical modules.

Missing fields and unknown codes remain unknown. All-one flag bytes stay raw,
without boolean conclusions. Readout freshness and source are explicit. These
fields never change the cover, animation, permissions or programming interlocks.
No controller setting, calibration, pairing or network change is required.

Menu-record decoding is audit-only in this beta. It does not add menu reads,
authenticate an installer role or unlock additional writes. Maneuver-slot decoding
has a native audit but still cannot establish freshness or newest-slot order.

## Native menu phase 2 and diagnostic fixes: v0.9.0b16

Select b16 in HACS and restart HA. No gate/controller changes or calibration are
required. The existing Controller automation state entity keeps its ID and enabled
selection. It uses an opt-in five-second timer while stationary, with failure backoff and no repeat of unsupported GETs; a snapshot older than
ten seconds, or contradicting the current cover state, is marked stale. Attributes
retain the captured reading and expose the current cover state separately.
Diagnostics downloads load metadata off HA's event loop, including a cold cache.

Optionally enable **Controller menu permissions**. This starts a read-only,
bounded group-4 menu inspection while stationary. Its entity reports completion, capture
age and counts for the separate user/installer columns; the full bounded table is
in downloaded diagnostics; `role_unverified` is expected until the
controller-role binding is established. It may become stale after thirty seconds;
enabled inspections are refreshed while idle. No additional setting is unlocked
by enabling this entity, and no listed parameter or password is automatically read.
An unsupported or denied menu request remains an explicit failure, not an empty
success. Download sanitized diagnostics through the GitHub web UI and include
version, controller/firmware and capture time for any mismatch with the app.

The next missing evidence is the actual controller-role authentication contract
and a normal-use menu comparison. Do not change passwords, activate installer
procedures or deliberately trigger a fault to collect it. Additional controls
require a separately verified role adapter, fresh menu/INFO and existing guards.

## Pro controls: v0.9.0b17

Enable Pro controls through Nice's integration options to expose supported
controller settings using the same installer menu columns as the reviewed Pro
app path. HA verifies module-admin access and a complete current menu before
enabling the mode. Enabling does not change a gate parameter or read/change its
PIN. Device denials and all existing scalar/INFO, movement, calibration,
session/model, bounds/options and one-send/readback checks remain active.

Eligible additional number/select controls are enabled and visible after explicit
opt-in. Which editors appear depends on the controller's actual menu and complete
scalar metadata. Unknown/indexed structures and unimplemented installer/receiver
procedures remain separate gaps; there is no arbitrary register-write interface.

B16's proposed separate controller-role proof was an integration restriction,
not an established requirement of the reviewed normal Pro SET path. B17 removes
that blocker without claiming a controller authentication role or bypassing device
authorization. The app-local PIN comparison and installer menu selection are
documented in the native audit.

## Pro editor usability: v0.9.0b18

Pro dropdowns now show the decoded option names with their numeric codes, such
as `5: Flashing light`. Existing legacy selects keep their translated `code_*`
states, and Pro select services continue to accept advertised `code_*` aliases.
Unknown meanings retain an explicit numeric fallback.

Healthy menu renewals retain the previous still-valid snapshot while the new
one is read, preventing editor availability flicker. A failed/invalid replacement
still revokes the grants, and an expired or wrong-session snapshot never becomes
authority. Actual writes continue to await a fresh serialized menu/INFO check.

## Final-phase inspection set: v0.9.0b19

The enabled Pro mode discovers the additional supported scalar editors as their
complete INFO becomes available. This includes slowdown positions and command
reaction/mode fields from the controller's own menu. Unknown/indexed formats still
do not become editors. Enabling Pro mode or discovering a control sends no SET.

Optionally enable **Receiver diagnostics** for a passive read of the existing
radio bus slot. It reports model/version, coding/type, channel flags/raw timers
and aggregate transmitter counts where supported. It does not scan remote codes
or credentials. Unsupported fields remain unknown/unsupported, not zero. These
are captured diagnostic values with age, not a continuous physical relay monitor.
Signed protocol capability bytes require a scoped challenge exchange and are not
interpreted as plain flags in this first receiver inspection set.
