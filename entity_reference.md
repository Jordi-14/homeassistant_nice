# Entity Reference

This file describes what each Home Assistant entity is for and how it is created
by default.

For daily use, the `cover` entity is the main dashboard entity. It provides
open, stop, close, current position, and set-position support when position data
is available. The gate `switch` is a simpler on/off duplicate for users who want
that style of control. The `Gate open` binary sensor exposes the same read-only
open/not-fully-closed signal for security and alarm automations without adding
another control surface.

The cover and `Gate position` sensor use the same displayed position. A
controller must first supply a real numeric position before either entity can
show a percentage. State-only controllers never get inferred `0%`, `100%`, or
time-based position data. On controllers with real encoder DMP status this is
normally the real percentage.
On devices with validated live T4 position frames, such as CU_WIFI percentage
frames or RBA4R10-style raw `0..7000` `04/40` frames, it may be a coarse real
percentage, then temporarily become a cached or simulated display value between
sparse updates. Check the cover attributes `real_position`, `display_position`,
`display_position_estimated`, and `position_simulation_action` when you need to
know whether the displayed percentage is fresh or estimated.

Real position can come from encoder registers such as
`04/11`, `04/18`, and `04/19`, or from validated live controller frames such as
CU_WIFI percentage `04/40` or RBA4R10 raw `04/40` when that mapping is known for
the device. Endpoint-only status can safely say open, closed, opening, closing,
or stopped, but it exposes no percentage at all.

Use `real_position` for automations that require a confirmed physical
percentage. Use `display_position` for dashboards where an approximate,
clearly-marked display value is acceptable. When `display_position_estimated` is
`true`, the displayed value may be held from the last known real value or
animated after a command; `position_simulation_action` shows the simulated
direction while that animation is active.

`Hidden` does not mean broken. It means Home Assistant creates and updates the
entity, but hides it from default views because it is diagnostic, advanced, or
not normally useful on a dashboard.

Entities ending in `setting` are writable BusT4 configuration entities. The
matching entities without `setting` are read-only views of the current
controller values. Change writable settings only while the gate is visible and
safe to operate, and write down the original values first.

Quick recommendations:

| Need | Use | Notes |
| --- | --- | --- |
| Daily open/close/stop | Gate cover | Best default dashboard entity. |
| Separate position display | Gate position | Same displayed percentage as the cover card; real only when `real_position` is available, cached/estimated when marked by the cover attributes. |
| Remote-control style action | Step-step | Follows the controller's configured step-step cycle. |
| Alarm open/not closed state | Gate open | Read-only binary sensor; on means the gate is not fully closed. |
| Pedestrian or partial opening | Partial open 1/2/3 | Partial open 1 is the common action; optional slots 2/3 are available only when their configuration registers are reported. Position follows the same measured-first rules as normal movement. |
| Local connection health | Connection state, last successful update, reconnect count | Useful for troubleshooting Wi-Fi or local API issues. |
| Controller tuning | Entities ending in `setting` | Advanced; these write controller registers. |
| Raw diagnostics | Diagnostics I/O byte and diagnostics parameters | Developer/debug data for comparing controllers. |
| Radio receiver info | OXI entities | Metadata from the OXI/radio endpoint when it answers locally. |
| Protocol event automations | Protocol event | Emits normalized state, obstruction, maintenance, diagnostic, BlueBUS, battery, motor-current, and reset event types when reported. |
| Event delivery health | Event stream state | Shows active delivery, polling fallback, or stopped state. |

## Protocol Events

The integration keeps one reader for each local connection. It correlates normal
request responses and routes unsolicited change, diagnostic, and live T4 frames
through the same reader. The existing cover, gate switch, gate-open sensor,
obstacle sensor, and maintenance counters continue to exist; event data updates
those entities sooner when the controller reports it. Adaptive polling remains
enabled and carries state if the event stream is lost.

The `Protocol event` entity exposes stable event types: `state_change`,
`obstruction`, `diagnostic`, `bluebus_error`, `battery`, `maintenance`,
`motor_current`, `reset`, and `unknown`. Its attributes are bounded normalized
codes and values. They do not contain raw protocol frames or accessory MAC
addresses.

Event-specific diagnostic entities are capability-adaptive. A controller that
explicitly reports that it does not support a field will not get the new
optional entity; an unknown controller retains the entity so newly discovered
device families are not hidden prematurely.

Mode settings such as `Photo close mode setting` and `Always close mode setting`
are raw Nice mode bytes. They are exposed as `0`-`255` values because tested
controllers can report values outside a small enum range. They are not seconds,
percentages, or decoded options. Strongly recommended: do not change them
unless you already know the exact byte your controller expects. A wrong raw mode
byte may leave that controller feature misconfigured.

Planned or investigated entities:

| Need | Status |
| --- | --- |
| Motor/controller temperature | Investigated. If implemented, it should be documented as a motor/controller diagnostic temperature, not as outdoor or ambient temperature. |

The table below describes the default entity registry behavior for a new
installation. Home Assistant preserves existing entity registry settings, so an
entity already created by an older version may keep its previous hidden or
disabled state.

Definitions:

- **Visible by default**: the entity is shown in Home Assistant when first added.
- **Hidden by default**: the entity is created and enabled, but hidden when first added.
- **Enabled by default**: the entity is created and updated by Home Assistant.
- **Disabled by default**: the entity is not created until manually enabled.

The defaults are intentionally split by expected use:

- Daily controls stay visible and enabled.
- Useful but advanced diagnostics are enabled and hidden.
- Duplicate, optional, risky, raw, or developer-facing entities are hidden.
- Entities that are noisy, experimental, or unsafe to change casually are
  disabled by default.

Writable BusT4 configuration entities are unavailable while the gate is moving.

T4 action buttons follow the controller's advertised `T4_allowed` bitmask.
Existing compatibility buttons remain registered but become unavailable when a
valid mask excludes them. Additional advanced buttons are created only when
their reviewed action bit is advertised. The integration rechecks support
before execution, so a stale entity cannot send an action excluded by the
current device profile.

| Platform | Entity | Key | Purpose | Visibility default | Enabled default | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Cover | Gate cover | `cover` | Main gate entity with open, stop, close, displayed position, and set-position support when position is available. | Visible | Enabled | Primary daily-use entity. |
| Switch | Gate switch | `cover_switch` | Simple on/off gate control: turn on opens, turn off closes, on means not closed. | Visible | Enabled | Useful for simple automations, but visually duplicates the cover. |
| Binary sensor | Gate open | `gate_open` | Read-only open/not-fully-closed state using the same state mapping as the gate switch: off only when closed, on when open/opening/closing/stopped. | Visible | Enabled | Alarm-friendly status entity with no control commands. |
| Switch | Auto close setting | `bus_t4_auto_close` | Writes BusT4 auto-close on/off to register `04/80`. | Visible | Enabled | Advanced but decoded enough to expose intentionally. |
| Switch | Photo close setting | `bus_t4_photo_close` | Writes BusT4 photo-close on/off to register `04/84`. | Visible | Enabled | Advanced but decoded enough to expose intentionally. |
| Switch | Always close setting | `bus_t4_always_close` | Writes BusT4 always-close on/off to register `04/88`. | Visible | Enabled | Advanced but decoded enough to expose intentionally. |
| Switch | Standby setting | `bus_t4_standby` | Writes BusT4 standby on/off to register `04/8C`. | Hidden | Enabled | Advanced controller setting. |
| Switch | Pre-flash setting | `bus_t4_pre_flash` | Writes BusT4 pre-flash on/off to register `04/94`. | Hidden | Enabled | Advanced controller setting. |
| Switch | Key lock setting | `bus_t4_key_lock` | Writes BusT4 key-lock on/off to register `04/9C`. | Hidden | Enabled | Advanced controller setting; not a daily dashboard control. |
| Button | Partial open 1 | `partial_open_1` | Sends the controller partial-open 1 action. | Visible | Enabled | Daily-use action when configured on the controller. |
| Button | Partial open 2 | `partial_open_2` | Sends the controller partial-open 2 action. | Visible | Enabled | Daily-use action when configured on the controller. |
| Button | Partial open 3 | `partial_open_3` | Sends the controller partial-open 3 action. | Visible | Enabled | Daily-use action when configured on the controller. |
| Button | Step-step | `step_step` | Sends the controller step-step action. | Visible | Enabled | Common remote-control style action. |
| Button | Courtesy light | `courtesy_light` | Sends the courtesy-light action. | Hidden | Enabled | Optional wiring/output; useful only on some installations. |
| Button | Courtesy light timer | `courtesy_light_timer` | Sends the courtesy-light timer action. | Hidden | Enabled | Optional wiring/output; useful only on some installations. |
| Button | Lock | `lock` | Sends T4 action `0x0F`, whose protocol meaning is block the automation. | Hidden | Disabled | Existing entity key retained for compatibility. Enabling it can prevent normal movement commands until released. |
| Button | Unlock | `unlock` | Sends T4 action `0x10`, whose protocol meaning is release the automation. | Hidden | Disabled | Existing entity key retained for compatibility. This is the protocol pair for the block action; it is not a physical lock-state sensor. |
| Button | Refresh status | `refresh_status` | Requests an immediate coordinator refresh. | Hidden | Enabled | Troubleshooting button, not a normal dashboard control. |
| Button | Reconnect | `reconnect` | Forces the local connection to reconnect. | Hidden | Enabled | Troubleshooting button, not a normal dashboard control. |
| Button | Calibrate positions | `calibrate_positions` | Runs the position calibration routine for intermediate set-position accuracy or time-based travel measurement. | Hidden | Disabled | Moves the gate repeatedly; users should enable it deliberately. |
| Button | Stop as remote | `stop_remote` | Sends the controller's remote-style stop action. | Hidden | Disabled | Redundant with the primary cover stop command; created only when advertised. |
| Button | Open as remote | `open_remote` | Sends the controller's remote-style open action. | Hidden | Disabled | Redundant with the primary cover open command; created only when advertised. |
| Button | Close as remote | `close_remote` | Sends the controller's remote-style close action. | Hidden | Disabled | Redundant with the primary cover close command; created only when advertised. |
| Button | Apartment step-step | `apartment_step_step` | Sends the apartment step-step action. | Hidden | Enabled | Device-specific action created only when advertised. |
| Button | Step-step high priority | `step_step_hp` | Sends the high-priority step-step action. | Hidden | Enabled | Device-specific action created only when advertised. |
| Button | Open and block | `open_and_block` | Opens and then blocks the automation. | Hidden | Disabled | Safety-sensitive action; enable deliberately only when its controller behavior is understood. |
| Button | Close and block | `close_and_block` | Closes and then blocks the automation. | Hidden | Disabled | Safety-sensitive action; enable deliberately only when its controller behavior is understood. |
| Button | Master door step-step | `master_step_step` | Sends step-step to the master door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Open master door | `master_open` | Opens the master door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Close master door | `master_close` | Closes the master door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Slave door step-step | `slave_step_step` | Sends step-step to the slave door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Open slave door | `slave_open` | Opens the slave door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Close slave door | `slave_close` | Closes the slave door. | Hidden | Enabled | Multi-door action created only when advertised. |
| Button | Release and open | `release_and_open` | Releases a blocked automation and opens it. | Hidden | Disabled | Safety-sensitive action created only when advertised. |
| Button | Release and close | `release_and_close` | Releases a blocked automation and closes it. | Hidden | Disabled | Safety-sensitive action created only when advertised. |
| Button | Enable BlueBUS inputs | `enable_bluebus_inputs` | Enables the controller's BlueBUS inputs. | Hidden | Disabled | Changes controller input behavior; enable only with the gate visible and the original state known. |
| Button | Disable BlueBUS inputs | `disable_bluebus_inputs` | Disables the controller's BlueBUS inputs. | Hidden | Disabled | Changes controller input behavior; enable only with the gate visible and the original state known. |
| Binary sensor | Closed limit switch | `limit_closed` | Experimental decoded closed-limit bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Disabled | Experimental and known not to work on the tested gate. |
| Binary sensor | Open limit switch | `limit_open` | Experimental decoded open-limit bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Disabled | Experimental and known not to work on the tested gate. |
| Binary sensor | Photocell | `photocell` | Experimental decoded photocell bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Disabled | Experimental and known not to work on the tested gate. |
| Binary sensor | Obstacle detected | `obstacle` | Indicates whether the latest stop reason points to an obstacle by encoder or force. | Hidden | Enabled | Potentially useful alert source, but not enough daily signal to show by default. |
| Binary sensor | Input 1 enabled | `input_1` | BusT4 input 1 configuration flag; this is not confirmed as a live input state. | Hidden | Disabled | Configuration flag, not a live input state. |
| Binary sensor | Input 2 enabled | `input_2` | BusT4 input 2 configuration flag; this is not confirmed as a live input state. | Hidden | Disabled | Configuration flag, not a live input state. |
| Binary sensor | Input 3 enabled | `input_3` | BusT4 input 3 configuration flag; this is not confirmed as a live input state. | Hidden | Disabled | Configuration flag, not a live input state. |
| Binary sensor | Input 4 enabled | `input_4` | BusT4 input 4 configuration flag; this is not confirmed as a live input state. | Hidden | Disabled | Configuration flag, not a live input state. |
| Binary sensor | Auto close | `auto_close` | BusT4 auto-close configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | Photo close | `photo_close` | BusT4 photo-close configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | Always close | `always_close` | BusT4 always-close configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | Standby | `standby` | BusT4 standby configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | Pre-flash | `pre_flash` | BusT4 pre-flash configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | Key lock | `key_lock` | BusT4 key-lock configuration flag. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Binary sensor | OXI receiver detected | `oxi_detected` | Indicates whether the local OXI endpoint answered at least one info register. | Hidden | Enabled | Useful compatibility diagnostic without dashboard value for most users. |
| Number | Pause time setting | `bus_t4_pause_time` | Writes BusT4 pause time to register `04/81` as a one-byte value. | Visible | Enabled | Advanced but bounded time setting. |
| Number | Opening force setting | `bus_t4_opening_force` | Writes BusT4 opening force to register `04/4A` as a one-byte value. | Visible | Enabled | Safety-sensitive motor tuning; change only while the gate is visible and the original value is known. |
| Number | Closing force setting | `bus_t4_closing_force` | Writes BusT4 closing force to register `04/4B` as a one-byte value. | Visible | Enabled | Safety-sensitive motor tuning; change only while the gate is visible and the original value is known. |
| Number | Opening speed setting | `bus_t4_opening_speed` | Writes BusT4 opening speed to register `04/42` as a one-byte value. | Visible | Enabled | Safety-sensitive motor tuning; unavailable on ARIA200S/CLBOX-style controllers until their speed format is confirmed. |
| Number | Closing speed setting | `bus_t4_closing_speed` | Writes BusT4 closing speed to register `04/43` as a one-byte value. | Visible | Enabled | Safety-sensitive motor tuning; unavailable on ARIA200S/CLBOX-style controllers until their speed format is confirmed. |
| Number | Photo close time setting | `bus_t4_photo_close_time` | Writes BusT4 photo-close time to register `04/85` as a one-byte value. | Visible | Enabled | Advanced but bounded time setting. |
| Number | Photo close mode setting | `bus_t4_photo_close_mode` | Writes BusT4 photo-close mode to register `04/86` as a raw one-byte value. This value is not decoded; avoid changing it unless you know the correct byte. | Hidden | Disabled | Raw controller byte; keep disabled unless explicitly needed for restore/testing. |
| Number | Always close time setting | `bus_t4_always_close_time` | Writes BusT4 always-close time to register `04/89` as a one-byte value. | Visible | Enabled | Advanced but bounded time setting. |
| Number | Always close mode setting | `bus_t4_always_close_mode` | Writes BusT4 always-close mode to register `04/8A` as a raw one-byte value. This value is not decoded; avoid changing it unless you know the correct byte. | Hidden | Disabled | Raw controller byte; keep disabled unless explicitly needed for restore/testing. |
| Number | Partial open 1 position setting | `bus_t4_partial_open_1_position` | Writes BusT4 partial-open 1 encoder position to register `04/21` as a two-byte value. | Visible | Enabled | Advanced but useful when partial-open positions need adjustment. |
| Number | Partial open 2 position setting | `bus_t4_partial_open_2_position` | Writes BusT4 partial-open 2 encoder position to register `04/22` as a two-byte value. | Visible | Enabled | Advanced but useful when partial-open positions need adjustment. |
| Number | Partial open 3 position setting | `bus_t4_partial_open_3_position` | Writes BusT4 partial-open 3 encoder position to register `04/23` as a two-byte value. | Visible | Enabled | Advanced but useful when partial-open positions need adjustment. |
| Number | Maintenance threshold setting | `bus_t4_maintenance_threshold` | Writes BusT4 maintenance threshold to register `04/B1` as a two-byte value. | Hidden | Disabled | Advanced but low operational risk. |
| Sensor | Connection state | `connection_state` | Current integration connection state. | Visible | Enabled | Primary health sensor. |
| Sensor | Last successful update | `last_successful_update` | Timestamp of the last successful coordinator update. | Hidden | Enabled | Useful health diagnostic. |
| Sensor | Last error | `last_error` | Last coordinator error, or `none`. | Hidden | Enabled | Useful troubleshooting diagnostic. |
| Sensor | Reconnect count | `reconnect_count` | Number of local reconnects performed by the client. | Hidden | Enabled | Useful health diagnostic. |
| Sensor | Last command | `last_command` | Last local command sent by the integration. | Hidden | Disabled | Developer/debug signal. |
| Sensor | Last command latency | `last_command_latency` | Latency of the last local command in milliseconds. | Hidden | Disabled | Developer/debug signal. |
| Sensor | Position calibration state | `position_calibration_state` | Current position calibration state. | Hidden | Enabled | Optional calibration detail; should not clutter default dashboards. |
| Sensor | Last position calibration | `last_position_calibration` | Timestamp of the last position calibration update. | Hidden | Enabled | Useful only when calibration is used. |
| Sensor | Position calibration error | `position_calibration_error` | Last calibration error, or `none`. | Hidden | Enabled | Useful only when calibration is used. |
| Sensor | Position calibration quality | `position_calibration_quality` | Quality grade for the current position calibration data. | Hidden | Enabled | Useful only when calibration is used. |
| Sensor | Position calibration report | `position_calibration_report` | Short calibration report with additional recorder-safe attributes. | Hidden | Disabled | Verbose troubleshooting entity. |
| Sensor | Gate position | `gate_position` | Displayed gate position percentage from the coordinator. | Visible | Enabled | Matches the cover card position; use cover attributes to distinguish confirmed real position from cached or simulated display values. |
| Sensor | Current encoder position | `current_encoder_position` | Current raw encoder position from the controller. | Hidden | Enabled | Useful advanced diagnostic; based on core status data. |
| Sensor | Closed encoder position | `closed_encoder_position` | Raw encoder value for the closed endpoint. | Hidden | Enabled | Useful advanced diagnostic; based on core status data. |
| Sensor | Open encoder position | `open_encoder_position` | Raw encoder value for the open endpoint. | Hidden | Enabled | Useful advanced diagnostic; based on core status data. |
| Sensor | Max open encoder position | `max_open_encoder_position` | BusT4 maximum open encoder position. | Hidden | Enabled | Useful for comparing controller position limits. |
| Sensor | Partial open 1 position | `partial_open_1_position` | BusT4 configured partial-open 1 encoder position. | Hidden | Enabled | Read-only mirror for partial-open configuration. |
| Sensor | Partial open 2 position | `partial_open_2_position` | BusT4 configured partial-open 2 encoder position. | Hidden | Enabled | Read-only mirror for partial-open configuration. |
| Sensor | Partial open 3 position | `partial_open_3_position` | BusT4 configured partial-open 3 encoder position. | Hidden | Enabled | Read-only mirror for partial-open configuration. |
| Sensor | Opening speed | `opening_speed` | BusT4 configured opening speed percentage. | Hidden | Enabled | Read-only mirror for controller tuning. |
| Sensor | Closing speed | `closing_speed` | BusT4 configured closing speed percentage. | Hidden | Enabled | Read-only mirror for controller tuning. |
| Sensor | Opening force | `opening_force` | BusT4 configured opening force percentage. | Hidden | Enabled | Read-only mirror for controller tuning. |
| Sensor | Closing force | `closing_force` | BusT4 configured closing force percentage. | Hidden | Enabled | Read-only mirror for controller tuning. |
| Sensor | Pause time | `pause_time` | BusT4 configured pause time. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Sensor | Maintenance threshold | `maintenance_threshold` | BusT4 configured maintenance threshold counter. | Hidden | Enabled | Read-only mirror for an advanced setting. |
| Sensor | Maintenance count | `maintenance_count` | BusT4 maintenance counter. | Hidden | Enabled | Useful maintenance diagnostic. |
| Sensor | Total maneuver count | `total_maneuver_count` | BusT4 maneuver counter discovered during community testing. | Hidden | Enabled | Useful maintenance/statistics diagnostic. |
| Sensor | Last stop reason | `last_stop_reason` | Decoded BusT4 last stop reason when the register is available. | Hidden | Enabled | Useful after unexpected stops. |
| Sensor | Motor temperature | `motor_temperature` | Decoded internal motor temperature from `04/D2` byte 15 minus 9. | Hidden | Enabled | Confirmed from issue #15 testing on Road 400 and RB1000; hidden because it is diagnostic. |
| Sensor | Service voltage | `service_voltage` | Decoded service voltage from `04/D2` byte 9. | Hidden | Disabled | Confirmed from issue #15 testing; disabled by default because it is advanced diagnostic data. |
| Sensor | Diagnostics I/O byte | `diagnostics_io_byte` | Raw `04/D1` diagnostics byte, displayed as hex for comparison. | Hidden | Disabled | Raw developer/debug data; decoded bits are not valid on the tested gate. |
| Sensor | Diagnostics parameters | `diagnostics_parameters` | Raw `04/D2` diagnostics parameter bytes for future decoding. | Hidden | Disabled | Raw developer/debug data. |
| Sensor | OXI product | `oxi_product` | Product string from the OXI/radio endpoint when available locally. | Hidden | Disabled | Optional metadata that is often unavailable. |
| Sensor | OXI firmware | `oxi_firmware` | Firmware string from the OXI/radio endpoint when available locally. | Hidden | Disabled | Optional metadata that is often unavailable. |
| Sensor | OXI hardware | `oxi_hardware` | Hardware string from the OXI/radio endpoint when available locally. | Hidden | Disabled | Optional metadata that is often unavailable. |
| Sensor | OXI description | `oxi_description` | Description string from the OXI/radio endpoint when available locally. | Hidden | Disabled | Optional metadata that is often unavailable. |
| Sensor | Interface firmware | `interface_firmware` | BiDi-WiFi interface firmware version from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Interface hardware | `interface_hardware` | BiDi-WiFi interface hardware version from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Interface serial | `interface_serial` | BiDi-WiFi interface serial number from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Control unit firmware | `control_unit_firmware` | Control unit firmware version from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Control unit hardware | `control_unit_hardware` | Control unit hardware version from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Control unit serial | `control_unit_serial` | Control unit serial number from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Sensor | Control unit product detail | `control_unit_product_detail` | Control unit detailed product identifier from INFO metadata. | Hidden | Enabled | Useful for support and compatibility reports. |
| Event | Protocol event | `protocol_event` | Emits normalized unsolicited local protocol events. | Visible | Enabled | Diagnostic automation source; attributes are bounded and exclude raw frames and MAC addresses. |
| Sensor | Event stream state | `event_stream_state` | Event delivery state: idle, active, polling fallback, or stopped. | Visible | Enabled | Polling continues when event delivery falls back. |
| Sensor | Last protocol event | `last_protocol_event` | Category of the latest normalized event. | Visible | Enabled | Includes bounded normalized details as attributes. |
| Sensor | Last protocol event at | `last_protocol_event_at` | Timestamp of the latest normalized event. | Hidden | Enabled | Event delivery diagnostic. |
| Sensor | Protocol event count | `protocol_event_count` | Number of normalized events handled since integration load. | Hidden | Enabled | Event delivery diagnostic. |
| Sensor | Malformed protocol event count | `malformed_protocol_event_count` | Number of unsolicited frames rejected by the event parser. | Hidden | Disabled | Developer diagnostic; malformed payload contents are not exposed. |
| Sensor | Last event cause code | `last_event_cause` | Latest raw controller cause code. | Hidden | Disabled | Created only when diagnostic event support is advertised or still unknown. |
| Sensor | Basic diagnostic code | `basic_diagnostic_code` | Latest basic diagnostic code. | Hidden | Disabled | Raw stable code; no proprietary description is inferred. |
| Sensor | Advanced diagnostic code | `advanced_diagnostic_code` | Latest advanced diagnostic code. | Hidden | Disabled | Raw stable code; no proprietary description is inferred. |
| Sensor | BlueBUS error status | `bluebus_error_status` | Latest BlueBUS error status code. | Hidden | Disabled | Raw controller code. |
| Sensor | Manoeuvre average current | `manoeuvre_average_current` | Latest numeric manoeuvre-current observation. | Hidden | Disabled | Unit is intentionally omitted until confirmed across device families. |
| Sensor | Last reset cause | `last_reset_cause` | Latest control-unit reset cause code. | Hidden | Disabled | Device-class code is included as a bounded attribute. |
| Sensor | Event battery level code | `event_battery_level` | Latest accessory battery code. | Hidden | Disabled | Includes accessory type but never its MAC address. |
| Binary sensor | Maintenance due | `maintenance_due` | On when the event-updated manoeuvre count reaches the maintenance threshold. | Hidden | Enabled | Uses the same existing maintenance values shown by the count and threshold sensors. |
| Binary sensor | BlueBUS fault | `bluebus_fault` | On when the latest BlueBUS status is not a known no-fault value. | Hidden | Disabled | Conservative raw-code interpretation for diagnostics. |
