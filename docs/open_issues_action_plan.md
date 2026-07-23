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

## Copy-paste issue responses for v0.7.6b0

These replies are ready to paste into the corresponding GitHub issues after
publishing the beta.

### Issue #9

```markdown
Thanks again for all the CU_WIFI testing. I have published a new beta:

https://github.com/Jordi-14/homeassistant_nice/releases/tag/v0.7.6b0

This beta focuses on the state/position disagreements we have seen in the live CU_WIFI frames.

The important changes for your RBS600HS are:

- `04/40` is now treated primarily as a position frame. Its opening/closing byte can confirm movement, but a coarse `stopped`, `open`, or `closed` value can no longer override the fresher NHK `DoorStatus` state.
- `04/02` remains a state source, but a state-only endpoint event no longer invents a `0%` or `100%` position.
- The cover attributes and diagnostics now show `state_source`, `position_source`, and `position_confidence`, so we can see exactly which path won.
- Live position must now show enough dense, monotonic movement before it can be used for target calibration. Coarse 20–30% jumps will fall back to time measurement instead of learning bad stop points.
- Set-position has a hard Stop deadline. A live update can make it stop earlier, but it cannot keep postponing Stop indefinitely.

Could you please update, restart Home Assistant, and test this sequence?

1. Start fully closed and open fully.
2. Open again and stop halfway, then verify that both open and close remain available.
3. Continue opening from the stopped position.
4. Close and stop halfway, then continue closing.
5. If possible, repeat one stop using the physical step-step input rather than Home Assistant.

Please check both state and position during the test. If anything is wrong, send a diagnostics export plus the cover attributes while stopped halfway. The new source/confidence attributes are especially useful.

There is no need to run another exhaustive probe yet. Diagnostics from this beta should be the best next step.
```

### Issue #11

```markdown
Thanks for the detailed ARIA200S / CLBOX tests. I have published a new beta:

https://github.com/Jordi-14/homeassistant_nice/releases/tag/v0.7.6b0

This beta makes the controller-specific safety behavior explicit:

- Opening and closing speed writes are blocked only when the detected identity contains both ARIA200S and CLBOX.
- The block is enforced in the coordinator as well as the number entities, so a service call cannot bypass it accidentally.
- Opening and closing force settings remain available because those worked normally in your tests.
- A controller that reports state but no real numeric position no longer gets synthetic `0%`, `100%`, time-based position, or set-position support in Home Assistant.
- Time calibration can still measure and report full-travel duration, but it will not pretend that the controller has a position sensor.
- Calibration cancellation now records the exact reason and writes a complete report.

Could you please update, restart Home Assistant, and check the following?

1. Confirm that Opening speed setting and Closing speed setting are unavailable.
2. Confirm that Opening force setting and Closing force setting are still available.
3. Test normal open, stop, and close without changing any controller settings.
4. With the gate visible and the path completely clear, run calibration once and send the calibration state/report.
5. Send a fresh diagnostics export after the test.

Please do not try to change the speed registers through services or other tools. We still need the real CLBOX speed encoding before those writes can be enabled safely. If an official Nice app shows the speed values or scale for this controller, screenshots of that page would still be very useful.
```

### Issue #14

```markdown
Thanks for the latest Road 400 / RBA4R10 results. I have published a new beta:

https://github.com/Jordi-14/homeassistant_nice/releases/tag/v0.7.6b0

The main goal for your case is to stop showing a fabricated `0%` after Partial open 1.

The new behavior is:

- A state-only `closed`, `open`, or `partially_open` report no longer creates a numeric position by itself.
- Valid RBA4R10 `04/40` raw position samples are still used when the controller sends them.
- Once the controller has demonstrated real numeric position reporting, the integration can bridge sparse updates with a clearly marked estimate. A real sample always wins.
- If the controller has not supplied any numeric position, Home Assistant shows no position and no set-position feature instead of inventing one.
- Partial open 2/3 buttons are unavailable when their configuration registers are absent. Partial open 1 remains available because that is the action your controller actually exposes.
- The diagnostic scripts now run without Home Assistant or `homeassistant-stubs`, and long DMP scans can checkpoint/resume.

Could you please test this exact sequence after updating and restarting?

1. Start fully closed and note whether position is available.
2. Run one full open and close cycle so we can see whether real `04/40` position is observed.
3. From fully closed, run Partial open 1.
4. Once it stops, copy the cover attributes, especially `real_position`, `display_position`, `display_position_estimated`, `position_reporting_observed`, `state_source`, `position_source`, and `position_confidence`.
5. Tell me the approximate physical opening and, if available, the value shown by MyNice Pro at the same moment.
6. Send a diagnostics export from immediately after the partial opening.

What I expect is that the incorrect fixed `0%` is gone. An exact partial-open percentage still requires a real live position sample. If the value is marked estimated, it is only bridging sparse samples; if no numeric sample has been observed, position should remain unavailable.

Please do not run another two-hour exhaustive probe for now. The new diagnostics and cover attributes should tell us whether a smaller focused capture is needed.
```

### Issue #22

```markdown
Thanks for digging into this. You were right that the `CancelledError` path was not writing the same complete report as success/failure.

I have published a new beta with that fixed:

https://github.com/Jordi-14/homeassistant_nice/releases/tag/v0.7.6b0

Calibration cancellation now records:

- the concrete cancellation reason, such as reconnect, shutdown, cover action, configuration write, DEP action, or set-position;
- elapsed time;
- the last calibration event and movement state;
- the last command;
- whether a Stop command was requested and whether it was actually sent;
- any Stop-command error.

It also writes the full bounded `Nice calibration report cancelled (...)` output to the Home Assistant logs and includes the cancellation reason/Stop outcome in diagnostics.

Could you please update, restart Home Assistant, and run calibration once with the gate visible and the path completely clear?

During that test, please avoid using the cover, changing settings, reloading the integration, or restarting Home Assistant unless one of those actions is what normally causes the cancellation.

If calibration completes, send the summary report. If it is cancelled again, please send:

1. Position calibration state and error.
2. The calibration section from a diagnostics export.
3. Every log chunk beginning with `Nice calibration report cancelled`.
4. Whether any automation, physical input, cover command, reconnect, or integration reload happened at roughly the same time.

That should finally tell us whether this is an internal timeout/failure or an external action cancelling the task.
```
