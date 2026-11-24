# Onboarding, sessions and module access

Phase 5 adds direct enrollment and module-user administration for the shared NHK
Wi-Fi interfaces. Manual credentials and one-time MyNice import remain available.
CORE and ProView retain their separate, blocked access adapters.

## Choose a setup method

| Method | Prerequisite | Result |
| --- | --- | --- |
| Manual credentials | Existing NHK username, device password, MAC and controller/source ID | Reuses existing module access |
| MyNice import | Existing manufacturer account and approved device access | Imports per-device NHK credentials, then discards account password and token |
| Direct first-user pairing | An unpaired module, reachable local TLS endpoint and its printed setup code | Sends one unsigned PAIR with the source-backed setup check; requires an explicit returned administrator role |
| Direct access request | An already configured module and an administrator willing to approve HA | Sends one unsigned PAIR without a setup check, then checks approval with VERIFY |
| Provisioning inspection | A reachable module AP TLS endpoint and its MAC | Reads WNC INFO/SCAN, reports the model and network count; does not configure Wi-Fi |

Use the setup code exactly as printed, including separators. The first-user check
uses the complemented CRC32 of the code, reversed challenge bytes and `Nice4U`.
BiDi enrollment uses the returned 32-byte password. The CORE password derivation
is represented separately and is never substituted for a missing BiDi password.

Direct pairing is an explicit module mutation. Upgrading an existing entry,
importing credentials and normal discovery do not pair, delete users, reset the
module or change the network. Discovery of an AP cannot overwrite an operational
entry. Cloud-only setup continues to support manual credentials and MyNice import;
direct pairing requires a local endpoint.

## Interrupted enrollment

Before PAIR can reach the wire, HA atomically saves the generated username and
controller/source ID in `nice_bidiwifi.pairing_intents`. The record contains no
setup code, device password, Wi-Fi password or cloud token. Two flows cannot claim
the same request simultaneously. After an uncertain response, a restart or a
reopened setup flow, HA keeps that identity and uses VERIFY instead of replaying
PAIR. A known rejection or a failure confirmed before dispatch permits a deliberate
retry with the same identity.

An active `admin` or `user` role and a complete returned password are required to
create the entry. A role-only VERIFY cannot manufacture missing credentials. If a
firmware never returns the password again after interrupted enrollment, use the
manufacturer app/manual credential recovery for that same access record; do not
reset the module or repeatedly create new users. The durable intent is removed
only when setup sees a persisted config entry bound to that exact identity.

## Inspect and manage users

Open the integration's Configure menu. Inspection shows the current module role
and approved/pending/admin counts, plus a local list of names and roles. Approval and permission screens list eligible
records locally so you can select the intended person/device. These identities
are transient and are not saved as integration options or exported in diagnostics.
The optional, disabled-by-default **Module permission** sensor reports `admin`,
`user`, `disable`, `wait` or `unverified`, with count-only metadata.

| Operation | Rules |
| --- | --- |
| Inspect | Signed PAIRINGS; roles are matched by exact username and controller ID |
| Approve | Existing pending request, approved as an ordinary user via USER_NEW |
| Edit | Existing approved record via USER_EDIT; admin, user, disabled or waiting permission |
| Promote | Another approved user can become a module administrator |
| Disable | Retains the paired record; does not delete it |
| Change HA's own access | Blocked to preserve the integration's current access |
| Remove the sole administrator | Blocked |
| Delete a paired record or transfer cloud ownership | Explicitly unavailable pending verified transactions and recovery |

Permission writes require explicit confirmation, a known stationary gate, no
calibration, a complete fresh user list and current administrator evidence. The
client rereads the list under the session lock immediately before writing and
requires the selected record to remain unchanged. It sends once, then reads back
the target's role. After any uncertain outcome, inspect again before retrying.

Configuration writes, interface naming, clock/time-zone synchronization and reboot
also require a freshly verified administrator role. Unknown permissions do not
become administrator rights. Existing ordinary gate control remains governed by
the module; a known waiting/disabled role is rejected locally too. Role evidence
is scoped to the active route and session generation, invalidated by reconnect or
reload, and refreshed periodically. Existing hardware/menu permission checks are
still required: module administrator status is not proof of installer-menu rights
and does not mean cloud ownership.

Identify sends one explicit unsigned IDENTIFY request while stationary. Its visible
effect depends on the module. It is never sent automatically during discovery.

## Sessions and recovery

Diagnostics distinguish disconnected, authenticating, authenticated, invalidated,
pairing-identity rejection, other authentication rejection and session backoff.
They contain error codes and retry delays, never credentials or session keys.

- A read rejected with NHK Code 3 or 4 may establish a new session on the same
  socket and repeat the read once. A second rejection is an authentication failure.
- Writes are never replayed by this recovery. An invalid session is cleared for
  the next operation, and the write outcome remains explicit.
- CONNECT Codes 7 and 15 retain bounded recovery: delays of 2 and 4 seconds,
  then cooldown until the five-minute window ends after the third rejection.
- CONNECT Code 1 remains an unresolved pairing/credential/session investigation.
  It does not trigger automatic PAIR, module reset or unlimited auth retries.
- Socket/connect failures remain separate from a valid device rejection. Setup
  reports uncertain delivery separately from failure before dispatch.

## Wi-Fi and remaining evidence gaps

Read-only provisioning uses protocol namespace `WNC`, requests `INFO` and `SCAN`,
and the existing TLS transport. Network rows and SSIDs remain transient; diagnostic
output contains counts only. AP addresses must be reachable from HA.

The source-backed CONFIG codec includes WLAN SSID plus Base64 SSID, security,
Base64 key, key length, band/country and `CONF Mode=NHK, Check=true`. It deliberately
omits cloud URL/port changes. The mutation adapter is blocked **before transport**:
complete AP-state/identity responses and operational rediscovery/recovery are not
yet verified. The UI therefore does not collect a Wi-Fi password or offer an Apply
operation. Use the manufacturer app for provisioning/reconfiguration for now.

The [gap register](protocol_gaps.md) records the exact missing evidence and completion
criteria for Wi-Fi recovery, deletion/ownership, explicit cloud registration,
CORE/ProView access and CONNECT Code 1. Filling a wire opcode alone cannot enable
a workflow that also needs state, authorization, completion and recovery checks.

Source basis: MyNice Welcome 1.3.1 NHKProtocolImpl, NHKPair/Verify/Pairings and
USER_NEW/USER_EDIT models, Algorithms setup checks, WNC request/response models and
Welcome provisioning call sites; cross-checked against the supplied MyNice Pro
2.18 exports. Tests use synthetic fixtures. Enrollment, permission writes and AP
reads still need appropriate hardware beta validation; they have not been run on
an established installation as part of this implementation.
