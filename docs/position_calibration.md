# Position, State, and Calibration

This document explains how the integration reports gate state, position, and
calibration data.

## Position Model

The cover exposes Home Assistant's native position support. Nice controllers do
not all expose position through the same path, so the integration separates two
concepts:

- `real_position`: a position percentage backed by a controller position
  source.
- `display_position`: the value shown by the cover and the `Gate position`
  sensor.
- `display_position_estimated`: `true` when `display_position` is simulated or
  held from the last known value.
- `position_simulation_action`: `open` or `close` while the integration is
  animating the displayed position after a command.

Use `real_position` for automations that need a confirmed physical percentage.
Use `display_position` only when a dashboard-friendly value is acceptable.

Home Assistant covers do not have a separate visual state for "stopped
mid-travel". A stopped half-open gate normally appears as open with a
percentage, for example `Open - 42%`, while both open and close actions remain
available.

## Position Sources

The integration currently knows four practical position-source classes.

### Encoder Raw

This is the NewRobus/BiDi-WiFi style path. The controller exposes DMP encoder
registers:

```text
percent = (04/11 - 04/19) / (04/18 - 04/19) * 100
```

Where:

- `04/11` is the current encoder position.
- `04/18` is the open endpoint.
- `04/19` is the closed endpoint.

This is the best source. It supports real intermediate position, raw endpoint
bounds, calibrated stop correction, and reliable final-position checks.

### Live Percent

Some CU_WIFI devices do not answer the normal DMP encoder reads, but expose NHK
state plus live T4 `04/40` frames where the position value is already `0..100`.

This source has no raw encoder endpoints, but it can still support target
calibration because the integration can see a real percent while the gate moves
and after it stops.

### Live Scalar

Some older controllers, such as the RBA4R10 case from issue #14, expose live T4
`04/40` values as a raw scalar instead of a direct percent. Earlier beta code
treated those values as a fixed `0..7000` range. The standardized calibration
now learns the observed closed/open scalar bounds during full travel and stores
them in the calibration profile.

After calibration, incoming raw scalar frames are normalized with the learned
bounds before they are used for display or set-position timing.

### Time Only

Some devices only report state: open, opening, closing, closed, and sometimes
stopped. Issue #11 ARIA200S/CLBOX currently belongs in this class unless future
probe data shows a real intermediate position source.

Time-only devices can learn full-travel duration and direction speed. They
cannot learn real overshoot correction at 20%, 40%, 60%, or 80%, because the
integration has no way to verify where the gate actually stopped.

## State Sources

The original DMP state register is:

- `04/01 = 01 ff 00 00` -> stopped
- `04/01 = 02 ff 00 00` -> opening
- `04/01 = 03 ff 00 00` -> closing
- `04/01 = 04 ff 00 00` -> open
- `04/01 = 05 ff 00 00` -> closed

For devices that reject the normal DMP status path, the integration can use:

- NHK `DoorStatus` from live `STATUS` / `CHANGE` frames.
- T4 `04/40` frames for live position and sometimes a coarse state byte.
- T4 `04/02` frames for movement or endpoint state.

A transient `DoorStatus=unknown` is treated as sparse status, not as a
connection failure. If a `04/40` frame says `stopped` while NHK `DoorStatus`
still says the gate is moving and the position is clearly intermediate, the
integration keeps the moving state and only uses the position from that frame.

## Calibration Purpose

Calibration is optional. If you only need open, stop, and close, do not
calibrate.

Calibration helps the set-position service and dashboard slider land closer to
intermediate targets. The integration cannot tell the motor "go to 60%"; it can
only send `open` or `close`, then send `stop` at the best known moment. The gate
keeps moving for a short time after `stop`, so the final position depends on
direction, speed, controller latency, inertia, and the position source.

The calibration button is a hidden-by-default diagnostic button. Unhide it only
when the gate is visible, the path is clear, and you are ready for the gate to
move repeatedly for several minutes.

## Standardized Calibration Sequence

Calibration now uses one shared decision flow:

1. Read the initial status and choose the best visible source.
2. If encoder bounds are available, use `encoder` mode.
3. If encoder bounds are missing, start in non-encoder mode and move fully
   closed.
4. Measure a full open and a full close while watching for live position frames.
5. If live `04/40` percent frames appear, promote to `live_percent`.
6. If live `04/40` raw scalar frames appear, promote to `live_scalar` and learn
   closed/open scalar bounds from the full-travel observations.
7. If no real intermediate source appears after the full-travel samples, stay in
   `time` mode.
8. For measured sources, calibrate opening targets from closed: 20%, 40%, 60%,
   and 80%.
9. Move fully open, then calibrate closing targets from open: 80%, 60%, 40%,
   and 20%.
10. Finish by closing the gate.

The physical target sequence is the same for every source that can measure
intermediate position: encoder raw, live percent, and live scalar. Time-only
devices use the same full-travel measurement runner, but they do not run
per-target correction attempts because there is no real final position to
measure after each stop.

If no measured source is found, the time profile measures up to three full open
and close runs and stores the median duration for each direction. This preserves
the robust time-based behavior while avoiding fake target corrections.

## Target Correction

For each measured target, calibration starts from a known endpoint, moves toward
the target, sends `stop` near the current learned threshold, waits for the gate
to settle, then records:

- requested target percentage;
- percentage where the stop command was sent;
- final settled percentage;
- error from target;
- corrected stop percentage for the next attempt;
- command latency;
- movement duration;
- raw value when the source provides one.

Each target can try up to five attempts. The stored correction prefers two
consecutive stable attempts within 2%. If no stable pair exists, it chooses the
best non-outlier attempt. Attempts that are still moving after the settle
timeout are marked invalid and are not used for learning.

Later set-position calls interpolate between stored correction points. For
example, if the user requests 60% and calibration learned that stopping at 52%
settles closest to 60%, the integration sends `stop` around the learned 52%
threshold.

## Profile Modes

The calibration profile stores a `mode`:

- `encoder`: DMP encoder registers were used.
- `live_percent`: live T4 position was already a percentage.
- `live_scalar`: live T4 raw values were normalized with learned bounds.
- `time`: only full-travel timing was available.

The report exposes the same value as `profile_mode`.

## Practical Examples

### Normal Open, Stop, and Close

Calibration is not involved when you press open, stop, or close. The integration
sends the matching command and then updates state from the best available status
source.

Examples:

- Encoder device: state and position come from DMP registers.
- CU_WIFI/live-position device: state may come from NHK `DoorStatus`, while
  position may come from live T4 frames.
- Endpoint-only device: state can still be open, opening, closing, closed, or
  stopped, but there is no confirmed intermediate percentage.

### Set Position Without Calibration

If the controller reports real intermediate position but has no calibration
profile yet, the integration can still try set-position by watching the live
position and sending `stop` when the target is crossed.

Example:

1. The gate is closed.
2. The user requests 50%.
3. The integration sends `open`.
4. A live position update eventually reports 50%.
5. The integration sends `stop`.

This can overshoot because the gate keeps moving while the position update is
received, processed, and the stop command travels back to the controller.

If the device has no real or estimated current position, intermediate
set-position cannot start safely. Endpoint requests still work: 0% sends close,
and 100% sends open.

### Set Position With Encoder, Live Percent, or Live Scalar Calibration

Measured-source calibration learns where to send `stop` so the gate settles near
the requested target.

Example from closed:

1. Calibration learned that asking for 60% settles closest to 60% when the stop
   command is sent around 52%.
2. The user requests 60%.
3. The integration sends `open`.
4. It sends `stop` around the learned 52% threshold.
5. The gate physically coasts and settles near 60%.

The opening and closing tables are separate. A good opening stop threshold may
not be correct while closing because the gate can move at a different speed and
coast differently in each direction.

### Live Scalar Calibration

Live-scalar devices report raw values instead of direct percentages. Calibration
learns the raw bounds during full travel.

Example:

1. Full close/open observations show closed around raw `1200` and open around
   raw `5200`.
2. A later raw live frame reports `3200`.
3. The integration normalizes that as roughly 50%.
4. Target calibration uses the normalized percentage table just like the encoder
   and live-percent modes.

This avoids relying on a fixed raw range when a controller's real travel span is
different.

### Time-Only Calibration

Time-only calibration is for devices that do not report real intermediate
position. It measures full-travel duration and uses that timing as an estimate.

Example:

1. Full opening takes 20 seconds.
2. Full closing takes 24 seconds.
3. From closed, a request for 50% opens for about 10 seconds and sends `stop`.
4. From open, a request for 50% closes for about 12 seconds and sends `stop`.

This is useful for display animation and approximate set-position. It is not a
real position sensor. If the gate slows down, starts from an unexpected point, is
interrupted, or moves differently in cold weather or under load, the displayed
percentage can drift from the physical position.

### Stopping Halfway

On measured-source devices, stopping halfway should preserve the latest real
position when the controller reports one. If the last real frame was sparse or
delayed, the displayed position may temporarily hold the last known value or use
simulation until a better update arrives.

On time-only devices, a stopped halfway position is estimated. It can be useful
for the dashboard, but automations should not treat it as confirmed physical
position.

### Sparse or Strange Live Frames

Some CU_WIFI devices report coarse position only every few seconds. Some also
send a `04/40` frame whose state byte says `stopped` while NHK `DoorStatus`
still says the gate is opening or closing. If the position is clearly
intermediate, the integration keeps the moving state and uses only the position
from that frame.

Implausible live jumps are not trusted for target timing. If the integration
expects the gate to be moving steadily toward 60% and a stale frame suddenly
claims an impossible jump, calibrated timing remains the safer source.

### When To Recalibrate

Recalibrate after changes that can affect travel time or position scaling:

- motor firmware update;
- controller replacement;
- gate speed or force changes;
- mechanical service that changes travel distance or friction;
- switching from a hardcoded live-scalar range to learned live-scalar bounds;
- repeated set-position overshoot after an integration update.

You do not need to recalibrate just because Home Assistant restarted.

## Reporting and Logs

During calibration the integration polls every 0.5 seconds and waits 0.5 seconds
after a stop or fully reached endpoint before sending the next movement command.

Calibration writes Home Assistant log lines with the prefix `Nice calibration:`.
It also exposes a hidden-by-default diagnostic sensor named `Position
calibration report` with recorder-safe summary attributes. When calibration
finishes or fails, the full detailed report is written to Home Assistant logs in
chunks with the prefix `Nice calibration report`.

The report includes:

- source mode and bounds;
- full-travel speed per direction;
- target points and selected attempts;
- max and average error;
- invalid or failed points;
- command latency;
- all calibration events.

## Safety Notes

Only run calibration when the gate is visible and the path is clear. The
integration deliberately avoids inventing position on devices that do not report
it. A time-only profile can improve animation and approximate stop timing, but
it is not a real position sensor.

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
