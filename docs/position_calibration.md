# Position, State, and Calibration

This document explains how the integration reports gate state, position, and
calibration data.

## Behavior

The integration keeps a persistent local NHK/TLS connection. If the BiDi rejects
the session or MyNice temporarily occupies the connection, the integration closes
the socket, marks the cover unavailable, and retries later instead of hammering
the device.

The cover exposes Home Assistant's position support. For intermediate targets,
the integration sends `open` or `close`, calculates a calibrated stop time, and
sends `stop` at the best known moment. When a real position update arrives while
the gate is still moving, the integration recalculates the remaining time from
that fresh position. On the validated BiDi-WiFi/NewRobus path this can be backed
by real encoder-derived DMP position. On CU_WIFI devices that expose validated
live T4 percentages, the same UI can use that coarse live value to correct the
timing, then may keep a cached or simulated display position between sparse
updates. This is intentionally coarse and should not be treated as millimeter
precision.

The integration does not treat a fully time-inferred intermediate percentage as
real controller position. Endpoint-only devices can safely tell Home Assistant
that the gate is open, closed, moving, or stopped, but they cannot prove that a
stopped half-open gate is exactly 18%, 42%, or any other intermediate value
unless a real position source reports it. Time-based calibration is therefore an
approximation for display animation and set-position timing, not a replacement
for encoder or validated live controller position data.

The cover attributes separate the raw source from the user-facing display:

- `real_position` is the latest real percentage from the device, if available.
- `display_position` is what Home Assistant shows in the cover and the
  `Gate position` sensor.
- `display_position_estimated` is `true` when the displayed percentage is
  currently simulated or held from the last known value.
- `position_simulation_action` is `open` or `close` while the integration is
  animating the displayed position after a movement command; otherwise it is
  `null`.

Use `real_position` for automations that need a confirmed physical percentage.
Use `display_position` only when a dashboard-friendly value is acceptable.

Home Assistant covers do not have a separate visual state for "stopped
mid-travel". A stopped half-open gate normally appears as open with a
percentage, for example `Open - 18%`, while both open and close actions remain
available.

## Calibration

Calibration is not required. If you only want to open, stop, and close the gate,
do not calibrate; normal open/close control works without it. Calibration only
helps if you want Home Assistant's position slider or set-position service to
land closer to intermediate positions such as 20%, 40%, 60%, or 80%.

The calibration button is a hidden-by-default diagnostic button. Unhide it only
when the gate is visible, the path is clear, and you are ready for the gate to
move repeatedly for several minutes.

When you press the calibration button, the integration first checks whether the
device exposes raw encoder position data.

## Calibration With Encoder Data

With encoder data available, the integration:

1. Moves fully closed first so the first opening test starts from a known
   physical position.
2. From closed, calibrates opening targets at 20%, 40%, 60%, and 80%.
3. For each target, returns to the known endpoint, moves toward the target,
   sends `stop` near the learned stop threshold, waits for the gate to settle,
   records the raw encoder value, final percentage, error, command latency, and
   movement timing, then repeats until it has made five attempts.
4. Moves fully open so closing tests also start from a known physical position.
5. From open, repeats the same five-attempt process for closing targets at 80%,
   60%, 40%, and 20%.
6. If the full calibration sequence completes, finishes by closing the gate. If
   calibration fails, it leaves the gate where it stopped so it does not
   surprise someone by closing after an external interruption.

The reason for this sequence is that gates do not stop instantly. The final
position depends on direction, speed, controller latency, inertia, and the raw
encoder value at the moment `stop` is sent. Calibration learns a
direction-specific stop table from real motion instead of assuming that sending
`stop` exactly at 40% will settle at 40%.

For each target, the stored stop threshold comes from the best stable evidence:
two consecutive attempts within 2% when available, otherwise the best
non-outlier attempt. If the gate is still moving after the settle timeout,
calibration sends another `stop` command and records that try as invalid instead
of learning from a moving position. Later intermediate position requests use the
calibrated table and interpolate between neighboring points when possible.

## Calibration Without Encoder Data

Without encoder data, the integration uses a lower-confidence time-based
calibration:

1. Moves fully closed first.
2. Measures three full openings from closed to open.
3. Measures three full closings from open to closed.
4. Stores every timing sample and selects the median duration for each
   direction.
5. Stores direction-specific full-travel durations and speeds.

This time-based profile improves display animation and can provide approximate
set-position timing by moving for the calculated duration and sending `stop`.
If validated live percentages arrive during that movement, they are used to
readjust the remaining stop time. The profile does not learn precise per-target
stop corrections unless the integration can verify the final position after each
stop.

During calibration it polls every 0.5 seconds and waits 0.5 seconds after a
stop or fully reached endpoint before sending the next movement command.

Calibration writes detailed Home Assistant log lines with the prefix
`Nice calibration:`. It also exposes a hidden-by-default diagnostic sensor named
`Position calibration report` with recorder-safe summary attributes. When
calibration finishes or fails, the full detailed report is written to Home
Assistant logs in chunks with the prefix `Nice calibration report`. The full
report includes a quality grade, max/average error, failed points, all attempts
per target, command latency, movement duration, and the event log.

## Known State and Position Sources

For the validated BiDi-WiFi/NewRobus encoder path, position is calculated as:

```text
(04/11 - 04/19) / (04/18 - 04/19) * 100
```

Where:

- `04/11` = current encoder position
- `04/18` = open/max encoder value
- `04/19` = closed/min encoder value

State values:

- `04/01 = 01 ff 00 00` -> stopped
- `04/01 = 02 ff 00 00` -> opening
- `04/01 = 03 ff 00 00` -> closing
- `04/01 = 04 ff 00 00` -> open
- `04/01 = 05 ff 00 00` -> closed

For devices that do not answer the normal DMP position reads, the beta fallback
can also use live T4 events:

- NHK `DoorStatus` from live `STATUS` / `CHANGE` frames for movement state.
  A transient `unknown` value is treated as sparse status, not as a connection
  failure, so the cover remains available while waiting for a useful state or T4
  position frame.
- T4 `04/40` frames for coarse percentage. Some CU_WIFI devices report this
  directly as `0..100`; some RBA4R10 controllers report raw `0..7000` values
  that the integration scales to percent. If one reports `stopped` while NHK
  `DoorStatus` still says the gate is moving and the percentage is not near an
  endpoint, the integration keeps the movement state and only uses the position.
- T4 `04/02` frames for movement or endpoint state.

Those live T4 values are expected to be less smooth than the real encoder path.
They are used for displayed real position and, when plausible during motion, to
correct calibrated set-position timing. They are not made user-configurable and
out-of-range or implausibly fast jumps are ignored.

Some BusT4 diagnostic and configuration entities depend on DMP registers that a
CU_WIFI controller may not expose. Those entities stay hidden or disabled by
default when they are advanced/raw, and otherwise become unavailable when the
controller does not return the required value. The integration does not invent
missing configuration values.

## Dashboard Slider

The integration exposes Home Assistant's native cover position feature. To show
a horizontal slider in a dashboard, use a Tile card with the cover position
feature:

```yaml
type: tile
entity: cover.your_gate_entity
features:
  - type: cover-position
  - type: cover-open-close
```

The exact layout of Home Assistant's built-in cover detail dialog is controlled
by the Home Assistant frontend, not by this integration.
