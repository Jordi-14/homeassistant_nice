# Native decoder follow-up: three phases

This extends the eight-phase app-parity work, starting from v0.9.0b14. HA owns
its automations, scenes, schedules and organization. This work supplies device
capabilities and manufacturer evidence, without duplicating those HA features.

## Phase 1: native audit and read-only decoding

Deliver a beta, v0.9.0b15, containing:

- A fingerprinted audit of the supplied native library, cross-referenced to the
  Java consumers and independent boundary/bit-vector regressions. Do not ship the
  vendor binary, assembly or decompiled bodies, or execute it in HA.
- The verified CTRL_AUTOMATION_STATUS byte decoder and an optional diagnostic
  entity. Correct the old blocked D3 placeholder to 04/01. Decode only fields
  actually present, keep raw unknown codes and mark all-one flags unverified.
  Do not feed diagnostic interpretations into movement, permissions or procedures.
- A pure, bounded menu-record decoder for auditing captures. Retain group/parameter
  identities and user/installer flag columns separately. This phase does not
  select an authenticated installer role, construct MenuPermission or grant writes.
- Native verification of the eight-slot maneuver decoder. Keep slot freshness,
  newest-slot order and physical cause unknown. Reject truncated input, including
  the native decoder's seven-byte out-of-bounds case, rather than reproducing it.
- Explicit evidence limits for INFO, I/O and hardware decoders already present.
  These have prior native-derived evidence; the new artifact does not itself
  establish every descriptor, controller model or indexed motor identity.

Exit: positive, missing, short, unknown, sentinel and oversized fixtures; full
software checks and verified release archive. Ordinary read-only comparison on
supported hardware remains a beta test, not a condition satisfied by static code.
Existing command guards, TLS serialization, entity IDs, learned motion and the
90% display animation remain in scope for regression validation.

## Phase 2: menu-driven controller configuration

Deliver a separate beta after completing these contracts:

- Verify menu request scope, profile/group, paging/continuation, malformed and
  denied replies, command maps and session ownership on supported devices.
- Reconcile decoded menu identities with INFO units, ranges, option lists and
  scalar/indexed layouts. Preserve per-motor identity as unknown until established.
- Follow the reviewed Pro menu-profile selection explicitly, retaining fresh
  module-admin access and device-enforced authorization. Do not invent an extra
  controller-role handshake. Scope menu/metadata evidence to the current session/target.
- Connect verified bindings to the existing parameter repository and write guards;
  require fresh permissions, stationary-state checks, one-send semantics and
  readback. Keep unknown or incomplete bindings read-only.

Exit: denied-role, reconnect, model mismatch, stale metadata, queue revocation and
ambiguous-delivery tests, plus a limited hardware/app comparison. Do not enable
all catalog writes solely because a menu record or native symbol exists.

## Phase 3: additional procedures and receiver capabilities

Deliver a separate beta for each coherent verified set:

- Audit receiver framing, scoped discovery, pagination and layouts; allowlist
  exported fields before retaining memory containing remote IDs, codes or keys.
- Add read-only receiver information first. Add enrollment/edit/removal only with
  complete target, consent, credentials, confirmation and recovery behavior.
- Improve model-specific history, per-motor diagnostics and installer procedures
  when the native audit and real captures establish all required contracts.
- Audit firmware, reset, learning and backup/restore individually. A native
  encoder alone does not establish image trust, physical completion or recovery.
  Leave genuine remaining gaps explicit with decoder/adapter completion points.

Exit: complete positive/negative and interruption fixtures, documented hardware
scope and a beta for normal use. Physical learning, reset, enrollment and firmware
operations are never performed automatically during upgrade or discovery.

## Evidence and testing policy

See [Native decoder audit](native_decoder_audit.md). The supplied arm64 library
matches the copy inside the Pro split APK. The complete Pro 2.18 base APK and
both arm64 splits now have verified v2/v3 signatures with the same signing certificate. Static interpretation,
fixture validation and hardware verification are distinct. No cloud mutation,
HA automation changes or live gate movement is required for phase 1 development.

## Phase 2 software delivery: v0.9.0b16

Implemented: addressed bounded menu paging, native sequence/FIN decoding,
complete-snapshot reconciliation, session/model/expiry fences, dynamic additional
control creation behind verified permissions and complete INFO, and the guarded
single-send/readback path. Existing settings keep their identities and default
compatibility behavior; a verified role adapter also applies controller denials
to those existing settings. Stale diagnostic readouts and blocking export reads
are corrected in the same beta. The enabled diagnostic has an independent timer,
failed reads back off, unsupported GETs are not probed repeatedly, and late polls
cannot publish a diagnostic snapshot across an observation-generation reset. The
menu entity exposes a compact summary; full bounded records are retained for
downloaded diagnostics, with no repeating table in recorder history.

The b16 role-proof requirement was too restrictive. Follow-up review found that
Pro compares its cached PIN locally and selects installer columns for normal
parameter writes; the reviewed path does not create a separate device-role token.
In b17, explicit Pro controls mode replaces that requirement. This is a menu-profile
selection, not a claim to authenticate as a controller installer. Module admin,
controller menu/INFO, session identity, bounds and device responses still apply.
No password read, password change or automatic parameter write is required.
Hardware/model comparison and genuinely unknown layouts remain beta evidence limits.

Phase 3 progress is tracked below. The original ownership boundary and prohibition
on automatic installer actions continue to apply.

## Phase 3 progress: v0.9.0b19 inspection set

Implemented in this set: passive receiver identity/type/coding, reported output
flags/raw timers and aggregate transmitter counts; strict signed-protocol codecs
requiring an explicit context; and an offline private remote-record decoder.
It also fills the 13 additional named scalar settings advertised by the live
controller menu. Pro metadata is discovered in small targeted batches, not by a
broad automatic scan for users who have not enabled Pro controls.

Still to complete in the final phase: the scoped radio challenge runtime and
capability readback; live memory-query/paging and credential/mutation contracts;
individual installer/reset/learning and firmware/restore audits. No first-step
inspection result should be described as completion of those operations.

### Next final-phase set: v0.9.0b20

The operator UI adds bounded, privacy-safe stored-slot inspection and a scoped
challenge transaction for signed capability reads. Ephemeral nonce SETs are not
configuration writes, but are explicit, single-send and not silently replayed or
moved to a second route after failure. Actual remote programming remains a separate
supervised procedure. Slot summaries do not expose identity codes or rolling
counters, and a page is never claimed as a complete backup.


### Final-phase verification and completion points: v0.9.0b21

B21 corrects signed capability inspection to the controller built-in radio
04/C1/BE/BF path; b20 incorrectly targeted the external receiver using map symbols.
Receiver inspection now retains only sanitized failure evidence, differentiates
explicit unsupported replies from generic failures, and suppresses old-session
operator results even before a passive snapshot exists. This makes hardware
trial results diagnosable without exporting private codes or exception text.

The complete signed Pro APK recovers InstallationFinder, bundled model groups,
FirmwareManagerImpl and receiver backup/restore bodies. Their absence is no longer
a blocker. The remaining contracts and exact inputs required to enable them are
listed in [Phase 3 completion audit](phase3_completion.md). A feature still lacking
its model/data/recovery contract stays unavailable. Publication does not mark
physical learning, reset, restore, remote programming or firmware as validated.

### Receiver inspection transport isolation: v0.9.0b22

Unknown answered receiver record formats use a separate domain error. They cannot
make the shared client reconnect/replay a valid read or erase the useful original
failure details. These results remain unverified formats, not guessed private
records or proof the firmware does not support a command. Tests cover a valid
16-byte unsupported record, no reconnect/replay, and sanitized wrapped causes.

### Bounded profile discovery: v0.9.0b23

The installer UI now exposes a read-only FF.FF STD_PROFILE inspection and public
identity reads for up to eight observed sources. This closes the basic address /
profile observation path without claiming complete BusT4 discovery or granting
peripheral permissions. Profile-specific control, full inventory completeness and
physical procedures retain their separate contracts and completion points.
