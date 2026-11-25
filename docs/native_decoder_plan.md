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
- Establish authenticated controller user/installer role selection separately
  from the module's admin role. Scope permissions to the current session/target.
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
matches the copy inside the Pro split APK; exact association with the exported
Pro 2.18 app still needs stronger packaging/version evidence. Static interpretation,
fixture validation and hardware verification are distinct. No cloud mutation,
HA automation changes or live gate movement is required for phase 1 development.
