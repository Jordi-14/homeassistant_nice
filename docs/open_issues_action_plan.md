# Open Issues Action Plan

Status reviewed: 2026-07-23. Target branch: `beta/issue-solving`.

This plan covers every currently open issue in
[`Jordi-14/homeassistant_nice`](https://github.com/Jordi-14/homeassistant_nice/issues).
The implementation is intentionally conservative: controller-reported data wins,
unsafe writes are blocked at the coordinator boundary, and a gate that has not
reported a real numeric position never exposes position data to Home Assistant.

## #9 — Alternate CU_WIFI position/status source

Issue: [Investigate alternate position/status source for Robus RBS600HS command-only devices](https://github.com/Jordi-14/homeassistant_nice/issues/9)

Action:

- Keep NHK `DoorStatus` as the normal state source for CU_WIFI devices that
  reject DMP status with `Code 14`.
- Treat `04/40` primarily as a position sample. Its opening/closing byte may
  confirm motion, but a coarse `stopped` byte must not override a fresher NHK
  motion state or invent a state when NHK is unknown.
- Keep `04/02` as the terminal/movement state event source, without converting
  endpoint state alone into `0%` or `100%`.
- Expose state source, position source, and confidence in diagnostics and cover
  attributes so future captures identify which source won.
- Require dense, monotonic live-position coverage before using a live source
  for per-target calibration. Coarse or discontinuous samples fall back to
  full-travel timing rather than learning false stop points.
- Keep set-position bounded by an original hard stop deadline. Live samples may
  shorten that deadline but may not postpone it indefinitely.

Close after a beta tester confirms opening, closing, local stop, physical-input
stop, and direction recovery on CU_WIFI hardware.

## #11 — ARIA200S / CLBOX position and unsafe speed settings

Issue: [Position calibration fails on ARIA200S (CLBOX)](https://github.com/Jordi-14/homeassistant_nice/issues/11)

Action:

- Treat ARIA200S / CLBOX as state-only until a real controller position source
  is observed. State remains available, but the cover exposes no position or
  set-position feature.
- Allow time-based full-travel calibration to produce timing diagnostics only;
  it must not create Home Assistant position data.
- Block writes to opening/closing speed registers `04/42` and `04/43` only when
  the combined identity contains both ARIA200S and CLBOX evidence.
- Enforce the speed block both in number-entity availability and in the
  coordinator write API so services or future callers cannot bypass it.
- Leave force registers available because the reporter confirmed those writes
  behave normally.
- Preserve the current displayed/last real position when Stop is sent on a
  position-capable controller, avoiding a stale endpoint snap after stopping.

Close position support only if a probe identifies a real position source.
Keep speed settings blocked until their controller-specific encoding is known.

## #14 — RBA4R10 position and partial-open behavior

Issue: [Position report on RBA4R10](https://github.com/Jordi-14/homeassistant_nice/issues/14)

Action:

- Retain support for reported RBA4R10 `04/40` raw scalar samples and normalize
  them only after a trustworthy range has been learned.
- Do not use state-derived endpoint percentages to seed time simulation. If the
  controller supplies no usable numeric position, full open, partial open, and
  physical switch movement all remain positionless in Home Assistant.
- On a controller that has reported real position, handle partial open through
  the same measured-first movement pipeline as normal open/close. A temporary
  estimate may bridge sparse samples and is explicitly marked as estimated.
- Keep partial-open 1 as the common controller action. Make optional partial-open
  2/3 buttons unavailable when their configuration registers are absent.
- Make diagnostic scripts runnable without installing Home Assistant or
  `homeassistant-stubs`.
- Add focused-scan resume and periodic checkpoint options so a multi-hour DMP
  probe can be split into safe, recoverable runs.

Close after a beta tester confirms raw scaling across a full cycle and verifies
that partial open no longer displays a fabricated `0%` on positionless hardware.

## #22 — Calibration cancellation without a report

Issue: [Calibration always ends as cancelled and no report is logged](https://github.com/Jordi-14/homeassistant_nice/issues/22)

Action:

- Give every integration-owned cancellation a concrete reason such as
  reconnect, shutdown, cover command, DEP action, configuration write, or
  set-position.
- Record elapsed time, last calibration event, last motion state, last command,
  whether Stop was requested, whether Stop was sent, and any Stop error.
- Build and log the same bounded calibration report for cancellation that is
  already produced for completion and failure.
- Surface the cancellation reason and Stop outcome in diagnostics.

Close after the reporter supplies one cancelled beta run whose Home Assistant
logs contain the complete `Nice calibration report cancelled (...)` output.

## Validation and release sequence

1. Run unit tests for parser arbitration, capability gating, write safety,
   calibration quality, hard stop timing, cancellation reporting, and scripts.
2. Run the complete test suite and Ruff checks.
3. Publish `beta/issue-solving` for hardware validation.
4. Ask each reporter to test only the scenarios listed for their controller.
5. Merge to `main` after hardware confirmation; do not close an issue merely
   because the branch compiles or simulated tests pass.
