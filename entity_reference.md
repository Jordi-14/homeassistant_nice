# Entity Reference

This file describes what each Home Assistant entity is for and how it is created
by default in the current beta.

For daily use, the `cover` entity is the main dashboard entity. It provides
open, stop, close, current position, and set-position support when position data
is available. The gate `switch` is a simpler on/off duplicate for users who want
that style of control.

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
| Separate position display | Gate position | Real percentage from the latest DMP status registers. |
| Remote-control style action | Step-step | Follows the controller's configured step-step cycle. |
| Pedestrian or partial opening | Partial open 1/2/3 | Uses the configured partial-open encoder positions. |
| Local connection health | Connection state, last successful update, reconnect count | Useful for troubleshooting Wi-Fi or local API issues. |
| Controller tuning | Entities ending in `setting` | Advanced; these write controller registers. |
| Raw diagnostics | Diagnostics I/O byte and diagnostics parameters | Developer/debug data for comparing controllers. |
| Radio receiver info | OXI entities | Metadata from the OXI/radio endpoint when it answers locally. |

Mode settings such as `Photo close mode setting` and `Always close mode setting`
are raw Nice mode bytes. They are exposed as `0`-`255` values because tested
controllers can report values outside a small enum range. They are not seconds,
percentages, or decoded options. Strongly recommended: do not change them
unless you already know the exact byte your controller expects. A wrong raw mode
byte may leave that controller feature misconfigured.

This table describes the default entity registry behavior for the current beta.
Home Assistant preserves existing entity registry settings, so an entity already
created by an older version may keep its previous hidden or disabled state.

Definitions:

- **Visible by default**: the entity is shown in Home Assistant when first added.
- **Hidden by default**: the entity is created and enabled, but hidden when first added.
- **Enabled by default**: the entity is created and updated by Home Assistant.
- **Disabled by default**: the entity is not created until manually enabled.

In `v0.7.0b8`, all integration entities are enabled by default. Beta diagnostic
and configuration entities are hidden by default so they can be tested by
unhiding them without having to enable disabled entities first.

Writable BusT4 configuration entities are unavailable while the gate is moving.

| Platform | Entity | Key | Purpose | Visibility default | Enabled default |
| --- | --- | --- | --- | --- | --- |
| Cover | Gate cover | `cover` | Main gate entity with open, stop, close, and set-position support when position is available. | Visible | Enabled |
| Switch | Gate switch | `cover_switch` | Simple on/off gate control: turn on opens, turn off closes, on means not closed. | Visible | Enabled |
| Switch | Auto close setting | `bus_t4_auto_close` | Writes BusT4 auto-close on/off to register `04/80`. | Hidden | Enabled |
| Switch | Photo close setting | `bus_t4_photo_close` | Writes BusT4 photo-close on/off to register `04/84`. | Hidden | Enabled |
| Switch | Always close setting | `bus_t4_always_close` | Writes BusT4 always-close on/off to register `04/88`. | Hidden | Enabled |
| Switch | Standby setting | `bus_t4_standby` | Writes BusT4 standby on/off to register `04/8C`. | Hidden | Enabled |
| Switch | Pre-flash setting | `bus_t4_pre_flash` | Writes BusT4 pre-flash on/off to register `04/94`. | Hidden | Enabled |
| Switch | Key lock setting | `bus_t4_key_lock` | Writes BusT4 key-lock on/off to register `04/9C`. | Hidden | Enabled |
| Button | Partial open 1 | `partial_open_1` | Sends the controller partial-open 1 action. | Visible | Enabled |
| Button | Partial open 2 | `partial_open_2` | Sends the controller partial-open 2 action. | Visible | Enabled |
| Button | Partial open 3 | `partial_open_3` | Sends the controller partial-open 3 action. | Visible | Enabled |
| Button | Step-step | `step_step` | Sends the controller step-step action. | Visible | Enabled |
| Button | Courtesy light | `courtesy_light` | Sends the courtesy-light action. | Visible | Enabled |
| Button | Courtesy light timer | `courtesy_light_timer` | Sends the courtesy-light timer action. | Visible | Enabled |
| Button | Lock | `lock` | Sends the controller lock action. | Visible | Enabled |
| Button | Unlock | `unlock` | Sends the controller unlock action. | Visible | Enabled |
| Button | Refresh status | `refresh_status` | Requests an immediate coordinator refresh. | Visible | Enabled |
| Button | Reconnect | `reconnect` | Forces the local connection to reconnect. | Visible | Enabled |
| Button | Calibrate positions | `calibrate_positions` | Runs the position calibration routine for intermediate set-position accuracy. | Hidden | Enabled |
| Binary sensor | Closed limit switch | `limit_closed` | Experimental decoded closed-limit bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Enabled |
| Binary sensor | Open limit switch | `limit_open` | Experimental decoded open-limit bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Enabled |
| Binary sensor | Photocell | `photocell` | Experimental decoded photocell bit from `04/D1`; not valid on the tested NewRobus `FG01h` data. | Hidden | Enabled |
| Binary sensor | Obstacle detected | `obstacle` | Indicates whether the latest stop reason points to an obstacle by encoder or force. | Hidden | Enabled |
| Binary sensor | Input 1 enabled | `input_1` | BusT4 input 1 configuration flag; this is not confirmed as a live input state. | Hidden | Enabled |
| Binary sensor | Input 2 enabled | `input_2` | BusT4 input 2 configuration flag; this is not confirmed as a live input state. | Hidden | Enabled |
| Binary sensor | Input 3 enabled | `input_3` | BusT4 input 3 configuration flag; this is not confirmed as a live input state. | Hidden | Enabled |
| Binary sensor | Input 4 enabled | `input_4` | BusT4 input 4 configuration flag; this is not confirmed as a live input state. | Hidden | Enabled |
| Binary sensor | Auto close | `auto_close` | BusT4 auto-close configuration flag. | Hidden | Enabled |
| Binary sensor | Photo close | `photo_close` | BusT4 photo-close configuration flag. | Hidden | Enabled |
| Binary sensor | Always close | `always_close` | BusT4 always-close configuration flag. | Hidden | Enabled |
| Binary sensor | Standby | `standby` | BusT4 standby configuration flag. | Hidden | Enabled |
| Binary sensor | Pre-flash | `pre_flash` | BusT4 pre-flash configuration flag. | Hidden | Enabled |
| Binary sensor | Key lock | `key_lock` | BusT4 key-lock configuration flag. | Hidden | Enabled |
| Binary sensor | OXI receiver detected | `oxi_detected` | Indicates whether the local OXI endpoint answered at least one info register. | Hidden | Enabled |
| Number | Pause time setting | `bus_t4_pause_time` | Writes BusT4 pause time to register `04/81` as a one-byte value. | Hidden | Enabled |
| Number | Opening force setting | `bus_t4_opening_force` | Writes BusT4 opening force to register `04/4A` as a one-byte value. | Hidden | Enabled |
| Number | Closing force setting | `bus_t4_closing_force` | Writes BusT4 closing force to register `04/4B` as a one-byte value. | Hidden | Enabled |
| Number | Opening speed setting | `bus_t4_opening_speed` | Writes BusT4 opening speed to register `04/42` as a one-byte value. | Hidden | Enabled |
| Number | Closing speed setting | `bus_t4_closing_speed` | Writes BusT4 closing speed to register `04/43` as a one-byte value. | Hidden | Enabled |
| Number | Photo close time setting | `bus_t4_photo_close_time` | Writes BusT4 photo-close time to register `04/85` as a one-byte value. | Hidden | Enabled |
| Number | Photo close mode setting | `bus_t4_photo_close_mode` | Writes BusT4 photo-close mode to register `04/86` as a raw one-byte value. This value is not decoded; avoid changing it unless you know the correct byte. | Hidden | Enabled |
| Number | Always close time setting | `bus_t4_always_close_time` | Writes BusT4 always-close time to register `04/89` as a one-byte value. | Hidden | Enabled |
| Number | Always close mode setting | `bus_t4_always_close_mode` | Writes BusT4 always-close mode to register `04/8A` as a raw one-byte value. This value is not decoded; avoid changing it unless you know the correct byte. | Hidden | Enabled |
| Number | Partial open 1 position setting | `bus_t4_partial_open_1_position` | Writes BusT4 partial-open 1 encoder position to register `04/21` as a two-byte value. | Hidden | Enabled |
| Number | Partial open 2 position setting | `bus_t4_partial_open_2_position` | Writes BusT4 partial-open 2 encoder position to register `04/22` as a two-byte value. | Hidden | Enabled |
| Number | Partial open 3 position setting | `bus_t4_partial_open_3_position` | Writes BusT4 partial-open 3 encoder position to register `04/23` as a two-byte value. | Hidden | Enabled |
| Number | Maintenance threshold setting | `bus_t4_maintenance_threshold` | Writes BusT4 maintenance threshold to register `04/B1` as a two-byte value. | Hidden | Enabled |
| Sensor | Connection state | `connection_state` | Current integration connection state. | Visible | Enabled |
| Sensor | Last successful update | `last_successful_update` | Timestamp of the last successful coordinator update. | Hidden | Enabled |
| Sensor | Last error | `last_error` | Last coordinator error, or `none`. | Hidden | Enabled |
| Sensor | Reconnect count | `reconnect_count` | Number of local reconnects performed by the client. | Hidden | Enabled |
| Sensor | Last command | `last_command` | Last local command sent by the integration. | Hidden | Enabled |
| Sensor | Last command latency | `last_command_latency` | Latency of the last local command in milliseconds. | Hidden | Enabled |
| Sensor | Position calibration state | `position_calibration_state` | Current position calibration state. | Visible | Enabled |
| Sensor | Last position calibration | `last_position_calibration` | Timestamp of the last position calibration update. | Hidden | Enabled |
| Sensor | Position calibration error | `position_calibration_error` | Last position calibration error, or `none`. | Hidden | Enabled |
| Sensor | Position calibration quality | `position_calibration_quality` | Quality grade for the current position calibration data. | Visible | Enabled |
| Sensor | Position calibration report | `position_calibration_report` | Short calibration report with additional recorder-safe attributes. | Hidden | Enabled |
| Sensor | Gate position | `gate_position` | Real gate position percentage from live DMP status registers. | Hidden | Enabled |
| Sensor | Current encoder position | `current_encoder_position` | Current raw encoder position from the controller. | Hidden | Enabled |
| Sensor | Closed encoder position | `closed_encoder_position` | Raw encoder value for the closed endpoint. | Hidden | Enabled |
| Sensor | Open encoder position | `open_encoder_position` | Raw encoder value for the open endpoint. | Hidden | Enabled |
| Sensor | Max open encoder position | `max_open_encoder_position` | BusT4 maximum open encoder position. | Hidden | Enabled |
| Sensor | Partial open 1 position | `partial_open_1_position` | BusT4 configured partial-open 1 encoder position. | Hidden | Enabled |
| Sensor | Partial open 2 position | `partial_open_2_position` | BusT4 configured partial-open 2 encoder position. | Hidden | Enabled |
| Sensor | Partial open 3 position | `partial_open_3_position` | BusT4 configured partial-open 3 encoder position. | Hidden | Enabled |
| Sensor | Opening speed | `opening_speed` | BusT4 configured opening speed percentage. | Hidden | Enabled |
| Sensor | Closing speed | `closing_speed` | BusT4 configured closing speed percentage. | Hidden | Enabled |
| Sensor | Opening force | `opening_force` | BusT4 configured opening force percentage. | Hidden | Enabled |
| Sensor | Closing force | `closing_force` | BusT4 configured closing force percentage. | Hidden | Enabled |
| Sensor | Pause time | `pause_time` | BusT4 configured pause time. | Hidden | Enabled |
| Sensor | Maintenance threshold | `maintenance_threshold` | BusT4 configured maintenance threshold counter. | Hidden | Enabled |
| Sensor | Maintenance count | `maintenance_count` | BusT4 maintenance counter. | Hidden | Enabled |
| Sensor | Total maneuver count | `total_maneuver_count` | BusT4 maneuver counter discovered during beta testing. | Hidden | Enabled |
| Sensor | Last stop reason | `last_stop_reason` | Decoded BusT4 last stop reason when the register is available. | Hidden | Enabled |
| Sensor | Diagnostics I/O byte | `diagnostics_io_byte` | Raw `04/D1` diagnostics byte, displayed as hex for comparison. | Hidden | Enabled |
| Sensor | Diagnostics parameters | `diagnostics_parameters` | Raw `04/D2` diagnostics parameter bytes for future decoding. | Hidden | Enabled |
| Sensor | OXI product | `oxi_product` | Product string from the OXI/radio endpoint when available locally. | Hidden | Enabled |
| Sensor | OXI firmware | `oxi_firmware` | Firmware string from the OXI/radio endpoint when available locally. | Hidden | Enabled |
| Sensor | OXI hardware | `oxi_hardware` | Hardware string from the OXI/radio endpoint when available locally. | Hidden | Enabled |
| Sensor | OXI description | `oxi_description` | Description string from the OXI/radio endpoint when available locally. | Hidden | Enabled |
| Sensor | Interface firmware | `interface_firmware` | BiDi-WiFi interface firmware version from INFO metadata. | Hidden | Enabled |
| Sensor | Interface hardware | `interface_hardware` | BiDi-WiFi interface hardware version from INFO metadata. | Hidden | Enabled |
| Sensor | Interface serial | `interface_serial` | BiDi-WiFi interface serial number from INFO metadata. | Hidden | Enabled |
| Sensor | Control unit firmware | `control_unit_firmware` | Control unit firmware version from INFO metadata. | Hidden | Enabled |
| Sensor | Control unit hardware | `control_unit_hardware` | Control unit hardware version from INFO metadata. | Hidden | Enabled |
| Sensor | Control unit serial | `control_unit_serial` | Control unit serial number from INFO metadata. | Hidden | Enabled |
| Sensor | Control unit product detail | `control_unit_product_detail` | Control unit detailed product identifier from INFO metadata. | Hidden | Enabled |
