# BusT4 Register Map

This page lists the BusT4 (T4) registers the integration reads, what each byte and
bit means, and on which controller model each meaning was confirmed. It is for
developers and for users who compare their controller with the integration. For
what each Home Assistant entity does, see [entity_reference.md](../entity_reference.md).

## How to read this page

A register is written `GG/PP`: group `GG`, parameter `PP`, both hexadecimal. The
integration reads registers with DMP requests through the BiDi-WiFi interface:

| Request | DMP flags | Reply operation | Gives |
| --- | --- | --- | --- |
| GET | `0x99` | `0x19` | the current value |
| INFO | `0x89` | `0x09` | the value format: size, range or allowed values |
| SET | `0xA9` | — | writes a value (only the `setting` entities do this) |

Addresses: the control unit is `00.03`; an OXI radio receiver is `00.0A`.

Reply status byte (`0x00` = OK). The integration treats every other status as
"no value"; the table is for reading probe results.

| Status | Meaning | Seen on ROBUSR10 FG01h |
| --- | --- | --- |
| `F0` | the register has no INFO | INFO of 04/01, 04/DA, STD group 00 |
| `F1` | GET is not allowed | GET of 04/08 |
| `F9` | the request needs a parameter (data bytes) | GET of 0A/25 on the OXI without a position |
| `FD` | the controller does not support this register | 147 of the 237 group 04 parameters |

INFO layout (MyNice Pro `decompileInfo`): byte 0 = value size (bits 0-6) and
little-endian flag (bit 7), byte 1 = data format, byte 2 = function, byte 3 =
flags. Flag bit 7 adds an array size of `flags & 3` bytes. Flag bit 6 = a list
follows (a count, then the entries); with bit 5 the list holds the allowed option
codes (1 byte each), without it one value of `size` bytes per field (04/D1,
04/D2). Without bit 6: min, max, step (`size` bytes each, in the byte order of
byte 0) and an optional 2-byte scale (always big-endian). Without bit 6, flag
bit 4 = divide by the scale, else bit 5 = multiply by it. See
`protocol/t4/info.py`.

Columns:

- **App code ✓**: read in the MyNice Pro app code.
- **Confirmed on**: the controller model on which someone saw this on a real
  gate. A meaning with neither is marked *guess*.
- **Read**: *poll* = every status poll; *extended* = the extended scan (about
  every 5 minutes); *start-up* = once per session (retried up to 3 times after a
  failure); *live* = pushed by the controller, not requested; *not read* =
  documented only.
- **(value)** after a model: the value was read on that model, but its effect on
  the gate was not tested.

### Models that confirmed data

| Model | Firmware | Interface | Source |
| --- | --- | --- | --- |
| ROBUSR10 (NewRobus, sliding) | FG01h, hardware 604ar00 | BiDi-WiFi SB725A1, firmware 2.6.4 | #55, #57–#67 (probes, gate cycles, MyNice Pro code) |
| RBA4R10 | — | — | #14 (live `04/40` position) |
| ARIA200S / CLBOX | — | — | #11 (state only, speed encoding not verified) |
| MC824H / HYKE | CD08 | BiDi-WiFi SB725A1, firmware 2.6.4 | #38 (open: forces may be per-motor arrays) |

To add a model, run the read-only `scripts/probe_register_map.py` (see
[probes.md](probes.md#register-map-probe)) and attach its report to an issue.

Values in the examples come from the ROBUSR10 above. Serial numbers, PINs, keys
and remote codes are never shown; where an example is needed it uses random
values of the same format.

## Groups

- [A. Identification](#a-identification)
- [B. State and position](#b-state-and-position)
- [C. Settings](#c-settings)
- [D. Diagnostics](#d-diagnostics)
- [E. OXI radio receiver](#e-oxi-radio-receiver)
- [F. Commands and procedures](#f-commands-and-procedures)
- [G. All group 04 parameters](#g-all-group-04-parameters)

## A. Identification

### STD group 00 — controller identity (address 00.03)

At address 00.03 the integration does **not** read these over DMP: the same
values come from the BiDi-WiFi INFO reply. They fill the Home Assistant device
page (manufacturer, model, model ID, firmware, hardware; see *Device information*
in [entity_reference.md](../entity_reference.md), #69) and the `control_unit_*`
sensors. They are listed so the DMP values can be compared. At the OXI receiver
the integration does read the same group over DMP (see group E).

| Register | App name | Example | App code | Confirmed on |
| --- | --- | --- | --- | --- |
| 00/04 | `STD_PROFILE` | `04` | ✓ | ROBUSR10 FG01h |
| 00/08 | (manufacturer) | `NICE` | — | ROBUSR10 FG01h |
| 00/09 | `STD_PRODUCT` | `ROBUSR10` (device model ID) | ✓ | ROBUSR10 FG01h |
| 00/0A | `STD_HARDWARE_VERSION` | `604ar00` | ✓ | ROBUSR10 FG01h |
| 00/0B | `STD_FIRMWARE_VERSION` | `FG01h` | ✓ | ROBUSR10 FG01h |
| 00/0C | `STD_RADIO_DESCRIPTION` | `NewRobus` (device model) | ✓ | ROBUSR10 FG01h |
| 00/20 | (product detail) | serial-like text, not shown; sensor `control_unit_product_detail` | — | ROBUSR10 FG01h |

Names in brackets are descriptions; the app constant was not checked. 00/08,
00/09-00/0C and 00/20 answer `F0` to INFO (no INFO).

### 04/00 — Automation type

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 1 | `F0` | not read | — (the BiDi-WiFi `device_type` gives the same) |

| Value | Meaning | App code | Confirmed on |
| --- | --- | --- | --- |
| `01` | sliding | ✓ | ROBUSR10 FG01h |
| `02` | sectional | ✓ | — |
| `03` | swing | ✓ | — |
| `04` | barrier | ✓ | — |
| `05` | up-and-over | ✓ | — |

The app maps the value with `ListDevice.valueOf`.

### 04/03 — Controller configuration

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET with index | 9 per element | `09 03 07 81 03`: 9-byte elements, array size 3 | start-up | `controller_configuration` |

Like MyNice Pro, the integration reads INFO array size − 1 elements, with the
indices 1 to array size − 1 (here 2 elements, indices 1 and 2), at most 16. Each
reply is the index byte followed by ASCII text, padded with `00`. The texts are
joined with `, `. If one element is missing, nothing is stored and the read is
tried again later.

| Index | Example (ROBUSR10) | Meaning | App code | Confirmed on |
| --- | --- | --- | --- | --- |
| 1 | `01 52 42 41 33 52 31 30 00` = `RBA3R10` | control board (*guess*) | — | ROBUSR10 FG01h (value) |
| 2 | `RB600` | motor (*guess*) | — | ROBUSR10 FG01h (value) |

The app reads and joins the elements (✓); the meaning of each one is a *guess* from the two values.
A reply whose index byte does not match the request is ignored (#61).

## B. State and position

### 04/01 — Automation state

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 4 | `F0` | poll | `cover` state (`state_source: dmp_04_01`) |

| Byte | Meaning | App code | Confirmed on |
| --- | --- | --- | --- |
| 0 | state, `EVT_CHANGE_STATE` table in `protocol/t4/enums.py`: `01` stopped, `02` opening, `03` closing, `04` open, `05` closed, `07` stopped in pause time, `10`-`12` stopped in partial 1-3 | ✓ | ROBUSR10 FG01h (`01`-`05`) |
| 1 | `ff` in every read | — | ROBUSR10 FG01h; meaning unknown |
| 2 | `00`; `02` or `03` in some reads, also in normal cycles | — | ROBUSR10 FG01h; meaning unknown |
| 3 | `00` in every read | — | ROBUSR10 FG01h; meaning unknown |

Example: `04 ff 00 00` = open.

### 04/02 — State change event

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| live event and controller history (`T4_LOGS`); GET answers `FD` | ≥ 2 | `FD` | live | `protocol_event`, `last_state_change_cause` |

The event class `04/02` is not the DMP parameter with the same number: in the app
map, DMP 04/02 is `CTRL_AUTOMATION_SLAVE_STATUS` (see group G).

| Byte | Meaning | App code | Confirmed on |
| --- | --- | --- | --- |
| 0 | new state, same table as 04/01 byte 0 plus events such as `83` opening from command, `88` stopped for command arrival, `91` force limiter intervention | ✓ | ROBUSR10 FG01h (`05`, `83`, `88`, `91`) |
| 1 | cause, `EVT_STATE_CAUSE` table in `protocol/t4/enums.py` | ✓ | ROBUSR10 FG01h |

Seen on the ROBUSR10:

- A photocell reversal gives `83 00` (opening from command, cause *none*), not
  a photocell cause. A remote-control reversal gives no 04/02 at all.
- Cause `19` (*oview*) comes with commands that arrive through the interface,
  for example every close sent from Home Assistant (`84 19`). It shows that a
  command arrived, not who sent it: on 2026-11-14 a stop with cause `19` came
  although Home Assistant sent no stop (#57). Stops that no command caused had
  cause `00` (*none*).
- When the leaf hit an obstacle: `91 00` (force limiter), then `83 00`.

### Encoder positions

All are 2-byte big-endian unsigned values. INFO on the ROBUSR10:
`02 25 14 01 00 00 ff ff 00 18` (range 0-65535, step 24). The step 24 is only the
+/- increment of the MyNice Pro input field; the controller accepted a write of
1001 to a partial position.

| Register | Name | Read | Entity | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/10 | transformation ratio | extended | `transformation_ratio` | `03 e8` = 1000 | ✓ | ROBUSR10 FG01h (value) |
| 04/11 | current position | poll | `cover` position, `current_encoder_position` | `14 73` = 5235 when open | ✓ | ROBUSR10 FG01h |
| 04/12 | maximum open position | extended | `max_open_encoder_position` | `14 73` = 5235 | ✓ | ROBUSR10 FG01h (value) |
| 04/13 | minimum closing position | extended | `min_closing_position` | `00 00` | ✓ | ROBUSR10 FG01h (value) |
| 04/18 | open position (end of travel) | poll | position scale, `open_encoder_position` | `14 73` = 5235 | ✓ | ROBUSR10 FG01h |
| 04/19 | closed position (end of travel) | poll | position scale, `closed_encoder_position` | `00 00` | ✓ | ROBUSR10 FG01h |
| 04/21 | partial open 1 position | extended | `partial_open_{n}_position`, W `bus_t4_partial_open_{n}_position` | `03 e8` = 1000 | ✓ | ROBUSR10 FG01h (value) |
| 04/22 | partial open 2 position | extended | `partial_open_{n}_position`, W `bus_t4_partial_open_{n}_position` | `0b b8` = 3000 | ✓ | ROBUSR10 FG01h (value) |
| 04/23 | partial open 3 position | extended | `partial_open_{n}_position`, W `bus_t4_partial_open_{n}_position` | `0f a0` = 4000 | ✓ | ROBUSR10 FG01h (value) |
| 04/24 | opening slowdown position | extended | `opening_slowdown_position` | `01 f4` = 500 | ✓ | ROBUSR10 FG01h (value) |
| 04/25 | closing slowdown position | extended | `closing_slowdown_position` | `01 f4` = 500 | ✓ | ROBUSR10 FG01h (value) |

The position in percent is `(04/11 − 04/19) / (04/18 − 04/19) × 100`. On the
ROBUSR10, 04/18 was between 5024 and 5271 in different sessions. During the
position-loss fault of 2026-11-14 it also read 39, 66 and 3018 (#57).

On the ROBUSR10 the slowdown was seen about 1100 counts before each end, while
04/24 and 04/25 read 500; how the controller uses these values is not known.

### 04/40 — Live position

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| live event only; GET answers `FD` | ≥ 3 | `FD` | live | `cover` position between polls |

As with 04/02, the live event class is not the DMP parameter: DMP 04/40 is
`CTRL_STR_SPEED_MANAGEMENT` in the app map and answers `FD` on the ROBUSR10.

| Byte | Meaning | Confirmed on |
| --- | --- | --- |
| 0 | state, same table as 04/01 byte 0 | ROBUSR10 FG01h (`02` opening, `03` closing) |
| 1-2 | position, big-endian | ROBUSR10 FG01h |

How the position is read depends on the frame (`protocol/t4/live.py`,
`position.py`):

| Frame | Conversion | Confirmed on |
| --- | --- | --- |
| target `FF.01`, value up to 7000 | raw scalar: `(raw − 04/19) / (04/18 − 04/19) × 100` when the encoder ends are known, a `live_scalar` calibration first, else `raw / 7000` | ROBUSR10 FG01h (#57 run 0: 135 of 135 events within ±2 % of the polled position); RBA4R10 (#14) |
| other target, value 0-100 | percent, used as it is | *guess*: some CU_WIFI devices, no model named |

See [position_calibration.md](position_calibration.md) for how each format is used.

## C. Settings

All settings are read in the extended scan. A read-only sensor or binary sensor
shows each value, except 04/85, 04/86, 04/89 and 04/8A, which only their
writable number shows. The registers marked **W** have a writable `bus_t4_*`
entity (number or switch). Once per session, after the first successful poll,
the integration reads the INFO of each writable setting (up to 3 tries); number
entities then use its range (#58). On known models some writes are refused (see
*Write restrictions* below); #63 (open) also refuses one-value writes when INFO
marks the value as a per-motor array.

INFO strings below are from the ROBUSR10 (FG01h). Other models can give other
ranges, which is why the integration reads INFO; a number entity uses the INFO
range within its own fixed limits.

### On/off settings

INFO `01 00 01 41 02 00 01`: 1 byte, list of values `00`, `01`.

| Register | Setting | W | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- |
| 04/80 | automatic close | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/84 | close after photo | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/88 | always close | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/8C | stand-by | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/90 | starting torque | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/94 | pre-flash | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/98 | slave mode | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/9A | automation blocked | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/9C | key lock | W | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/A2 | slowdown | | `01` | ✓ | ROBUSR10 FG01h (value) |

### Times

Plain seconds unless a scale is given. With scale 10 the raw value is tenths of a
second; the sensor shows seconds (#60).

| Register | Setting | W | INFO (min-max, step, scale) | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/81 | pause time | W | 0-250, 1 | `1e` = 30 s | ✓ | ROBUSR10 FG01h |
| 04/85 | photo close time | W | 0-250, 1 | `05` = 5 s | ✓ | ROBUSR10 FG01h (value) |
| 04/89 | always close time | W | 0-250, 1 | `05` = 5 s | ✓ | ROBUSR10 FG01h (value) |
| 04/5B | courtesy light time | | 0-250, 1 | `3c` = 60 s | ✓ | ROBUSR10 FG01h |
| 04/8D | stand-by time | | 10-250, 1 | `3c` = 60 s | ✓ | ROBUSR10 FG01h (value) |
| 04/95 | pre-flash time, opening | | 0-10, 1 | `03` = 3 s | ✓ | ROBUSR10 FG01h (value) |
| 04/99 | pre-flash time, closing | | 0-10, 1 | `03` = 3 s | ✓ | ROBUSR10 FG01h (value) |
| 04/5A | electric lock time | | 1-100, 1, ÷10 | `14` = 2.0 s | ✓ | ROBUSR10 FG01h (value) |
| 04/5C | suction cup time | | 1-100, 1, ÷10 | `14` = 2.0 s | ✓ | ROBUSR10 FG01h (value) |
| 04/91 | starting torque time | | 5-50, 1, ÷10 | `14` = 2.0 s | ✓ | ROBUSR10 FG01h (value) |

The courtesy light time was seen on the gate: 04/D2 counted down from 60 after
each stop (see 04/D2).

### Speeds and forces

INFO `01 00 0a 01 00 64 01`: 1 byte, 0-100 %, step 1.

| Register | Setting | W | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- |
| 04/42 | opening speed | W | `50` = 80 % | ✓ | ROBUSR10 FG01h (value) |
| 04/43 | closing speed | W | `46` = 70 % | ✓ | ROBUSR10 FG01h (value) |
| 04/45 | opening slowdown speed | | `14` = 20 % | ✓ | ROBUSR10 FG01h (value) |
| 04/46 | closing slowdown speed | | `14` = 20 % | ✓ | ROBUSR10 FG01h (value) |
| 04/4A | opening force | W | `64` = 100 % | ✓ | ROBUSR10 FG01h (value) |
| 04/4B | closing force | W | `64` = 100 % | ✓ | ROBUSR10 FG01h (value) |

The speed in 04/D2 is a measured value, not this setting: with 80 / 70 % set,
04/D2 showed 95-99 % opening and 71-84 % closing (ROBUSR10).

### Option settings

The INFO is a list of the codes this controller allows (flags `61`). The
integration shows the code by name from the tables in `protocol/t4/options.py`;
a code without a name shows as `code_<n>` in decimal (for example `ListOut` code
`23` = `code_35`). 04/86 and 04/8A have no names yet: their number entities show
the raw code.

| Register | Setting | W | App list (INFO function) | Allowed codes (ROBUSR10) | Example (ROBUSR10) | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/51 | output 1 function | | `ListOut` (`F4`) | 00 01 03 02 04 05 17 06 23 07 0b 0f 10 11 12 | `05` flashing light | ROBUSR10 FG01h |
| 04/52 | output 2 function | | `ListOut` (`F4`) | same | `01` gate-open indicator | ROBUSR10 FG01h |
| 04/61 | step-by-step mode | | `ListCommand` (`F3`) | 01-08 | `02` open-stop-close-open | ROBUSR10 FG01h (value) |
| 04/62 | partial open mode | | `ListCommand` | 01-05 07 08 | `02` | ROBUSR10 FG01h (value) |
| 04/63 | open mode | | `ListCommand` | 09-0d | `09` open-stop-open | ROBUSR10 FG01h (value) |
| 04/64 | close mode | | `ListCommand` | 0e 0f 10 12 | `0e` close-stop-close | ROBUSR10 FG01h (value) |
| 04/65 | stop mode | | `ListCommand` | 15 16 | `15` stop | ROBUSR10 FG01h (value) |
| 04/68 | photo reaction, closing | | `ListCommand` | 13 16 15 14 | `13` reverse | ROBUSR10 FG01h |
| 04/69 | photo reaction, opening | | `ListCommand` | 13 16 15 14 | `13` reverse | ROBUSR10 FG01h (value) |
| 04/6A | photo 3 reaction | | `ListCommand` | 14 15 | `14` stop movement | ROBUSR10 FG01h (value) |
| 04/6B | Alt reaction, opening | | `ListCommand` | 17 18 19 | `18` Alt and short reverse | ROBUSR10 FG01h (value) |
| 04/6C | Alt reaction, closing | | `ListCommand` | 17 18 19 | `18` Alt and short reverse | ROBUSR10 FG01h (value) |
| 04/78 | obstacle reaction, opening | | `ListCommand` | 17 18 19 | `19` Alt and reverse | ROBUSR10 FG01h (value) |
| 04/79 | obstacle reaction, closing | | `ListCommand` | 17 18 19 | `19` Alt and reverse | ROBUSR10 FG01h |
| 04/71 | input 1 function | | `ListDeviceCommand` (`F2`) | 00 01 05 03 04 02 06 07 0c 0d 19 0e 1a 0f 10 11 12 0b 13 14 15 16 17 18 1b 1c 21 22 23 25 26 | `01` step-by-step | ROBUSR10 FG01h |
| 04/72 | input 2 function | | `ListDeviceCommand` | same | `03` open | ROBUSR10 FG01h |
| 04/73 | input 3 function | | `ListDeviceCommand` | same | `04` close | ROBUSR10 FG01h |
| 04/74 | input 4 function | | `ListDeviceCommand` | 2b 2c 27 | `27` emergency stop | ROBUSR10 FG01h (value) |
| 04/86 | photo close mode | W | `ListFunctionsMode` (`F5`) | 10 11 | `11` | ROBUSR10 FG01h (value; names not mapped) |
| 04/8A | always close mode | W | `ListFunctionsMode` | 20 21 | `20` | ROBUSR10 FG01h (value; names not mapped) |
| 04/8E | stand-by mode | | `ListFunctionsMode` | 30 31 32 | `31` safety devices | ROBUSR10 FG01h (value) |
| 04/B0 | maintenance mode | | `ListFunctionsMode` | 05 04 | `05` automatic | ROBUSR10 FG01h (value) |

"Confirmed on" without "(value)" means the gate behaved as the value says (for example output 1
flashed during every move, the photocell reversed the gate); "(value)" means only
the value was read.

### Maintenance counters

4-byte big-endian unsigned values.

| Register | Name | W | INFO (min-max, step) | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/B1 | maintenance threshold | W | 0-4294967294, 10 | `00 00 1b 58` = 7000 | ✓ | ROBUSR10 FG01h (write 7010 and back, read back each time) |
| 04/B2 | maintenance count | | 0-4294967295, 1 | `00 00 04 95` = 1173 | ✓ | ROBUSR10 FG01h (value) |
| 04/B3 | total maneuver count | | 0-4294967295, 1 | `00 00 00 22` = 34 | ✓ | ROBUSR10 FG01h (value) |

04/B3 is `CTRL_MANEUVERS_COUNTER` in the app. It seemed to go up by one per
close/open cycle (*guess*). Open: on the ROBUSR10, 04/B3 (34) is lower than 04/B2 (1173), so the unit or
meaning of 04/B3 is not clear yet.

### Write restrictions

| Model | Registers | Rule | Source |
| --- | --- | --- | --- |
| ARIA200S / CLBOX | 04/42, 04/43 | writes blocked: speed encoding not verified | #11 |
| MC824H / HYKE | 04/4A, 04/4B | writes blocked unless valid INFO says the value is not a per-motor array, also after the 3 tries (#63, open; not tested on an MC824H) | #38 |
| any | any setting | writes blocked until the INFO read is done (at most 3 tries), and when INFO marks an array | #63 (open) |

Rules are in `models/profiles.py` and `coordinator.setting_write_block_reason`.

## D. Diagnostics

The decoders for 04/D1 and 04/D2 attributes and the `bluebus_gate_state` sensor
are in #65 (open). Without #65 these registers are shown as raw bytes. With #65
the INFO of 04/D1 and 04/D2 is read once per session, like the settings.

The ROBUSR10 samples "in motion" below come from a local test build that read
04/D0-04/D2 about once a second during gate cycles; the integration itself reads
them in the extended scan.

### 04/D0 — BlueBus devices

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 27 | `01 81 e0 01` | extended | `bluebus_devices`, `bluebus_gate_state` (#65) |

Decoder: `protocol/t4/bluebus.py` (MyNice Pro `setDiagPhotoStatus` @0x211f8 and the
device rows of the diagnostics screen).

| Byte | Bits | Meaning | App code | Confirmed on |
| --- | --- | --- | --- | --- |
| 0 | 0 | 1 = gate not moving, 0 = moving | — | ROBUSR10 FG01h (every sample in cycles 11-13) |
| 0 | 3 | together with byte 10: photocell *in error* | ✓ | — |
| 1 | 4 | BlueBus line error: every device shows `stand_by` | ✓ | — |
| 2 | 0-6 | photocell installed: PHOTO, PHOTO II, PHOTO 1, PHOTO 1 II, PHOTO 2, PHOTO 2 II, PHOTO 3 | ✓ | ROBUSR10 FG01h (PHOTO only) |
| 3 | 0-3 | FTA, FTD, FTB, FTC photocell installed | ✓ | — |
| 4 | 0-3 | CMD 1-4 installed | ✓ | — |
| 4 | 5, 6 | PHOTO OPEN, PHOTO OPEN II installed | *guess* (masks from the "not working" code) | — |
| 5 | 0 | S-SL installed | ✓ | — |
| 6 | 0-6 | photocell not working (order of byte 2) | ✓ | — |
| 9 | 0 | S-SL not working | ✓ | — |
| 7 | 0-3 | FT photocell not working | ✓ | — |
| 8 | 0-3, 5, 6 | CMD / PHOTO OPEN not working | ✓ | — |
| 10 | 0-6 | photocell error (with byte 0 bit 3) | ✓ | — |
| 11 | 0-6 | photocell active (with byte 19) | ✓ | ROBUSR10 FG01h (value: `7f` in every read) |
| 12 | 0-3 | gate state: `01` closed, `02` open, `04` closing, `08` opening, `00` stopped between the ends | — | ROBUSR10 FG01h (cycles 11-13: 50+ samples and 59 diagnostics files, also closed after 330 s at rest) |
| 12 | 7 | stand-by: every PHOTO device of byte 2 shows `stand_by` (not FT, CMD or S-SL) | ✓ | — (stand-by is off on the test gate) |
| 17 | 0-2 | S-SL status | ✓ | — |
| 19 | 0-6 | photocell beam: 1 = free, 0 = interrupted | ✓ | ROBUSR10 FG01h (beam blocked by hand during a close) |
| 20 | pairs | FT photocell on/off | ✓ | — |
| 21-24 | — | CMD 1-4 / PHOTO OPEN status (`21` = CMD 1 and PHOTO OPEN) | ✓ | — |

Byte 12 bits 0-3 repeat 04/01 on a healthy controller. A difference between the
two shows a controller that reports one state over BusT4 and another to its
BlueBus devices; that is why `bluebus_gate_state` exists (#65).

Example, gate open at rest:
`11 00 81 00 00 00 00 00 00 00 00 7f 02 00 00 00 00 00 00 01 00 c0 00 00 00 00 00`
→ PHOTO installed (byte 2 bit 0), beam free (byte 19 bit 0), gate open (byte 12),
not moving (byte 0 bit 0). Byte 0 bit 4, byte 2 bit 7 and byte 21 (`c0`) are also
set on the ROBUSR10; their meaning here is unknown (CMD 1 is not installed, so
byte 21 is not shown).

### 04/D1 — Inputs and outputs

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 14 | list of 14 "installed" masks: `0f 07 43 00 0d ff ff ff ff 3c 1f ff 01 00` | extended | `diagnostics_io_byte` (byte 2 as hex) with decoded attributes (#65), `controller_errors` |

Each item is one bit; the same bit in the INFO list says whether the controller
has it. When the INFO was read, only installed items are shown; without INFO all
items are shown. `controller_errors` does not check the INFO. Decoder: `protocol/t4/io.py` (MyNice Pro
`T4InputOutputDiagnostic_init` @0xf7d4).

| Byte | Bits | Items | App code | Confirmed on |
| --- | --- | --- | --- | --- |
| 0 | 0-5 | Alt input OK (1 = OK), input 1-3, loop 1-2 | ✓ | ROBUSR10 FG01h (value: Alt OK = 1) |
| 1 | 0-2 | buttons 1-3 | ✓ | — |
| 2 | 0-5 | limit open / close M1, M2; unlocked M1, M2 | ✓ | — (encoder-only gate) |
| 2 | 6 | direction: 1 = left (SX) | ✓ | ROBUSR10 FG01h (value: `1`) |
| 2 | 7 | selected motor: 1 = M1 | ✓ | — |
| 3 | 0-3 | encoder fault M1, M2 (1 = fault); encoder input M1, M2 | ✓ | — |
| 4 | 0-7 | outputs: motor M1, M2, output 1-3, fan, green light, red light | ✓ | ROBUSR10 FG01h (motor M1, output 1 flashing light, output 2 gate-open light change with the gate) |
| 5 | whole byte | type of the STOP/Alt input: `0` not set, `1` B1, `2` B2, `3` NC, `4` NO, `5` out of range, `6` sensitive edge | ✓ | ROBUSR10 FG01h (value: `04` NO) |
| 6 | 0-3 | radio inputs 1-4 | ✓ | — |
| 7 | 0-3 | T4 mode I inputs 1-4 | ✓ | — |
| 8 | whole byte | T4 mode II (number) | ✓ | — |
| 9 | 0-3, 5 | thermal, heating, stand-by, battery, automatic opening | ✓ | — |
| 9 | 4 | mains frequency: 0 = 50 Hz, 1 = 60 Hz | ✓ | — |
| 10 | 0-6 | controller errors: positions, BlueBus, Alt, functions, regulations, map 1, map 2 (1 = error) | ✓ | ROBUSR10 FG01h (value: all 0) |
| 11 | 0-1 | force limiter state: `0` OK, `1` level 1, `2` level 2, `3` motor alarm | ✓ | ROBUSR10 FG01h (value: always level 2) |
| 12 | 0-2 | overload output 1-3 | ✓ | ROBUSR10 FG01h (value: output 1 always 1) |
| 12 | 4-7 | overtravel low / high M1, M2 | ✓ | — |

Seen in 489 samples on the ROBUSR10 (rest and motion): every byte except byte 4
was constant, `01 00 40 00 xx 04 00 00 00 00 00 02 01 00`.

- Overload output 1 was 1 with the flashing light on and off, so it is not
  "output active". Its meaning is open.
- The force limiter showed level 2 at rest, in normal motion and during a
  photocell reversal. It is probably the set level, not an alarm (*guess*).

### 04/D2 — Hardware values

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 28 | list of 14 big-endian `u16` field scales | extended | `diagnostics_parameters` with decoded attributes (#65), `service_voltage`, `motor_temperature` |

Each field is a big-endian `u16` at offset 2 × index. In the INFO list, bit 15 of
each entry = field available, bits 0-14 = full scale *M*. A percent field is
`((value × 100) & 0xFFFC) // M`, limited to 0-255 (MyNice Pro `diag1DgxCalcPCT`;
for *M* > 650 the app first shifts value and *M* right, see `dgx_percent` in
`protocol/t4/io.py`). On the ROBUSR10 *M* = 100, so the raw value is already
percent.

| Index | Field | Seen on ROBUSR10 FG01h | App code |
| --- | --- | --- | --- |
| 0 | work time left (s) | 240 at the start of a move, −1 per second, 0 at the stop | ✓ |
| 1 | pause time left (s) | 30 after a photocell reversal | ✓ |
| 2 | courtesy light time left (s) | 60 after a stop, counts down | ✓ |
| 3 | bus current (%) | 11 in every sample | ✓ |
| 4 | service voltage | 34 at rest, 30-33 while moving (unit V is a *guess*) | ✓ |
| 5 | torque M1 (%) | 50-60 at the start, 20-45 running, 12-30 in the slowdown | ✓ |
| 6 | torque M2 (%) | not available (0) | ✓ |
| 7 | motor temperature | 15-28 raw (two days); signed `ldrsh` in the app | ✓ |
| 8, 9 | voltage M1, M2 (%) | not available (0) | ✓ |
| 10 | speed M1 (%) | 95-99 opening, 71-84 closing, 17-19 in the slowdown | ✓ |
| 11 | speed M2 (%) | not available (0) | ✓ |
| 12 | not used by the app or the integration | same value as 04/11 (5235 when open) | — |
| 13 | — | 0 | — |

The integration shows the motor temperature as raw − 9 °C. The app shows the raw
value; the −9 offset is not confirmed (#57).

### 04/DA — Last 8 maneuvers

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| GET | 8 | `F0` | extended | `last_maneuvers` |

One byte per maneuver, outcome from `AUTOMATION_STATUS_DIAGNOSTICS` in
`protocol/t4/enums.py`: `00` OK, `01` BlueBus error, `02` photo intervention,
`03` obstacle detected, `04` Alt detected, `05` internal parameters error, `06`
maneuvers per hour exceeded, `07` electric anomaly, `08` blocking command, `09`
automation blocked, `0a` obstacle detected by encoder. All `ff` = no data.

Seen on the ROBUSR10 FG01h:

- Byte 0 is the newest entry. A full cycle (close + open) moves the list by two.
- A close that the photocell reversed, followed by the reopening, gave **one**
  entry `02` (photo intervention) in the tests (*guess* that this is always so).
  A remote-control reversal gave `00`. A hit on an obstacle gave `03`.
- Read entry 0 after the gate stops to find why the last close did not finish
  (#64).

## E. OXI radio receiver

An OXI receiver plugged into the control unit answers at address `00.0A`. Its
identity uses the same STD group 00 as the control unit; the radio settings are
in group `0A`. The registers below answer `F0` to INFO (no INFO), except 0A/50
and 0A/51, whose INFO is OK but empty.

### STD group 00 at 00.0A — receiver identity

| Register | Name (app) | Read | Entity | Example | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 00/04 | STD_PROFILE | extended | — | `0a` | ✓ | OXI on ROBUSR10 FG01h |
| 00/09 | STD_PRODUCT | extended | `oxi_product` | `OXIBD group` | ✓ | OXI on ROBUSR10 FG01h |
| 00/0A | STD_HARDWARE_VERSION | extended | `oxi_hardware` | `672B` | ✓ | OXI on ROBUSR10 FG01h |
| 00/0B | STD_FIRMWARE_VERSION | extended | `oxi_firmware` | `BK00H` | ✓ | OXI on ROBUSR10 FG01h |
| 00/0C | STD_RADIO_DESCRIPTION | extended | `oxi_description` | `Radio` | ✓ | OXI on ROBUSR10 FG01h |

`oxi_detected` is on when address 00.0A sends any reply to these registers or to
0A/00 and 0A/41, even an error status. Before #66 the integration asked group
`0A` for the identity; the receiver answers `FD` there.

### 0A/00 — Receiver type

| Access | Size | Read | Entity |
| --- | --- | --- | --- |
| GET | 1 | extended | `oxi_product` attribute `receiver_type` |

Attribute values (`RADIO_RECEIVER_TYPES` in `protocol/t4/enums.py`): `01` `oxi`,
`02` `oxit`, `03` `ox2`, `04` `ox2t`, `05` `ox4`, `06` `ox4t`, `07` `oxifm`, `08`
`oxitfm`, more codes up to `20` in the same table; any other code shows as
`code_<n>`. The names come from the app enum `ListDevice`; that the wire value
maps to this enum is a *guess* except for `01`, confirmed on an OXI on ROBUSR10
FG01h.

### 0A/41 — Remote coding (RADIO_CFG_CODES)

| Access | Size | Read | Entity |
| --- | --- | --- | --- |
| GET | 1 | extended | `oxi_product` attribute `coding_type` |

The bits are active low. Bit 7 = 0 means the coding is defined; then the first
bit that is 0, checked in the order 4 (FLO), 6 (HCS), 5 (FLOR), names the coding
(MyNice Pro `RadioCfgCodingType`). Attribute values: `flo`, `hcs`, `flor`; no
attribute when bit 7 = 1 or no bit is 0. Example: `5f` → `flor`. App code ✓;
confirmed on an OXI on ROBUSR10 FG01h with FloR remotes.

### 0A/40 — Receiver configuration (RADIO_CFG)

| Access | Size | Read | Entity |
| --- | --- | --- | --- |
| GET | 2 | not read | — |

`status = byte0 << 8 | byte1`; a flag is **on when its bit is 0** (MyNice Pro
`RadioConfiguration`):

| Bit | Flag | Bit | Flag |
| --- | --- | --- | --- |
| 0 | repeater | 8 | PIN management |
| 1 | release button | 9 | lock memory |
| 2 | repetition on bus | 10 | block with enabling code |
| 3 | reception on bus | 11 | block with certificate |
| 4 | authorization groups | 12 | disable RND |
| | | 13 | only original transmitters |
| | | 14 | disable priority |

The OXI on the ROBUSR10 answers `00 7f`: by the app logic bits 8-14 are on and
bits 0-4 off. That the high byte is unused is a *guess*; it was not compared with
the MyNice Pro receiver screen yet. App code ✓; OXI on ROBUSR10 FG01h (value).

### 0A/25 — Remotes in memory

| Access | Size | Read | Entity |
| --- | --- | --- | --- |
| GET with 2 data bytes | 10 per remote | not read | — |

The request carries the memory position `[pos >> 8, pos & 0xFF]`, 0-1023 (MyNice
Pro `qryPosition`). Without it the receiver answers `F9`. The reply is
`[pos_hi, pos_lo, 7 bytes, 00]`; an empty slot has `ff` in the 8 bytes after the
position. Counting the remotes needs one request per position, up to 1024, so the
integration does not poll it. App code ✓; layout confirmed on an OXI on ROBUSR10
FG01h. Example of a used slot with random values: `02 c7 5a 3c 91 0e 02 7b 11 00`.

### Sensitive registers

These hold the receiver PIN, keys and certificates. Their sizes are listed so
nobody needs to read them; the integration never reads them and their values
must not be posted in issues.

Sizes seen on an OXI on ROBUSR10 FG01h; names from the app.

| Register | Content | Size |
| --- | --- | --- |
| 0A/10 | PIN | 4 |
| 0A/31 | certificates | 5 |
| 0A/32-0A/35 | Altera, distributor, installer, installation keys | 2 each |

Not to be sent at all: 0A/09 (send a command), 0A/20 (clear the memory), 0A/50
(send a code).

## F. Commands and procedures

### DEP actions — gate commands

Commands are sent as a DEP frame to `00.03` with arguments `01 82 <command> 64`
(`build_dep_action_frame` in `protocol/t4/actions.py`). The codes are the `01`-`1c`
part of the `ListDeviceCommand` list (the input functions 04/71-04/74 use the
same list). Names below follow `INPUT_FUNCTIONS`; the button names in
`T4_ACTIONS` differ for some codes (for example `0f`/`10` Lock / Unlock, `19`/`1a`
Release and open / close, `1b`/`1c` Enable / Disable BlueBUS inputs). The
integration has no action for partial close `08`-`0a`.

| Code | Command | Code | Command |
| --- | --- | --- | --- |
| `01` | step-by-step | `10` | unblock |
| `02` | stop | `11` | courtesy light timer |
| `03` | open | `12` | courtesy light on/off |
| `04` | close | `13`-`15` | master step-by-step / open / close |
| `05`-`07` | partial open 1-3 | `16`-`18` | slave step-by-step / open / close |
| `0b` | step-by-step condominium | `19`, `1a` | unblock and open / close |
| `0c` | step-by-step high priority | `1b`, `1c` | condominium open on / off (BlueBus inputs) |
| `0d`-`0f` | open and block, close and block, block | | |

App code ✓. No DEP command code is confirmed on hardware yet. On ROBUSR10 FG01h
open and close were tested through the cover entity, which sends the BiDi-WiFi
`DoorAction`, not a DEP frame.

### 04/08 — Accepted commands (CTRL_STR_COMMANDS)

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| INFO only; GET answers `F1` | 1 | list of the accepted command codes | not read | — |

On the ROBUSR10 FG01h the list has 25 codes: `01`-`07`, `0b`-`1c`. Partial close
`08`-`0a` is missing. The integration does not read 04/08: it shows a button only
when the interface lists the command in its T4 allowed mask
(`protocol/t4/allowed.py`). On ROBUSR10 FG01h that mask (`0x1FFFF8FE`) holds the
same 25 codes as 04/08.

### Procedures (not used by the integration)

These group 04 registers start installer procedures or moves in MyNice Pro,
probably by a SET (*guess*; how the app starts them was not traced). The
integration does not read or write them; they are listed so they are not probed
by accident. The "Moves the gate" and "Reversible" columns are *guesses* from the
app names; none of these registers was ever written.

| Register | Procedure | INFO on ROBUSR10 | Moves the gate | Reversible |
| --- | --- | --- | --- | --- |
| 04/0A | search BlueBus devices | `01 80 00 01` | no | the device table is replaced |
| 04/0B | search positions | `01 80 00 01` | **yes**, full travel | positions are replaced |
| 04/0C | delete parameters | list `00 01 02 03 7d` | no | **no** |
| 04/0F | go to a position (a move command, not a procedure) | `F0` | **yes** | — |
| 04/B4 | reset the maintenance counter | `01 00 01 41 02 00 01` | no | **no** |
| 04/05, 04/09, 04/27, 04/2F, 04/3F | search devices, activate receiver, delete intermediate slowdown, reset encoder, delete maps | `FD` | — | — |

## G. All group 04 parameters

The full list of the 237 group 04 parameters that MyNice Pro 2.18 knows
(`global_map_Commands` in `libnative-lib.so`, decoded in #54), with the names of
the app. **INFO** and **GET** show how a ROBUSR10 FG01h answered a one-off
read-only probe (`OK`, or the status code from *How to read this page*). Action
registers (search, delete, reset, activate, go to position, test, calibration)
got no GET on purpose: "not sent". 04/E9 (a time) was skipped by the same name
filter. **Map** ✓ = described in sections A-F; registers that share one row there
(04/05, 04/09, 04/27, 04/2F, 04/3F) have no ✓.

74 parameters answer GET or INFO with `OK` on the ROBUSR10; other models answer other sets.

| Register | App name | App description | INFO | GET | Map |
| --- | --- | --- | --- | --- | --- |
| 04/00 | `CTRL_AUTOMATION_TYPE` | Type automation | F0 | OK | ✓ |
| 04/01 | `CTRL_AUTOMATION_STATUS` | State automation | F0 | OK | ✓ |
| 04/02 | `CTRL_AUTOMATION_SLAVE_STATUS` | Slave state automation | FD | FD | ✓ |
| 04/03 | `CTRL_STR_VERSION_CONFIGURATION` | PCB version / configuration (only barriers) | OK | OK | ✓ |
| 04/04 | `CTRL_STR_MODULAR_CU_VERSION` | Modular control unit board version | FD | FD |  |
| 04/05 | `CTRL_SEARCH_DEVICES` | Search devices | FD | not sent |  |
| 04/06 | `CTRL_FUNCTION_MODE` | Function mode | FD | FD |  |
| 04/07 | `CTRL_RADIO_CONTROLS_MODE_2` | Radio controls mode 2 | FD | FD |  |
| 04/08 | `CTRL_STR_COMMANDS` | Commands | OK | F1 | ✓ |
| 04/09 | `CTRL_ACTIVATE_RECEIVER` | Activate receiver | FD | not sent |  |
| 04/0A | `CTRL_SEARCH_BLUEBUS_DEVICES` | Search Bluebus devices | OK | not sent | ✓ |
| 04/0B | `CTRL_SEARCH_POSITIONS` | Search positions | OK | not sent | ✓ |
| 04/0C | `CTRL_DELETE_PARAMETERS` | Delete parameters | OK | not sent | ✓ |
| 04/0D | `CTRL_INSTALLATIONS_TYPE` | Type of installation | FD | FD |  |
| 04/0F | `CTRL_CMD_QUOTA` | Command go to position | F0 | not sent | ✓ |
| 04/10 | `CTRL_TRANSFORMATION_RATIO` | Transformation ratio | OK | OK | ✓ |
| 04/11 | `CTRL_POSITION_CURRENT` | Current position | OK | OK | ✓ |
| 04/12 | `CTRL_POSITION_MAXIMUM_OPENING` | Maximum opening position | OK | OK | ✓ |
| 04/13 | `CTRL_POSITION_MINIMUM_CLOSING` | Maximum closing position | OK | OK | ✓ |
| 04/14 | `CTRL_INSTALLATION_FORCE` | Force used during quotes installation - multipage [1 = opening, 2 = closing] | FD | FD |  |
| 04/15 | `CTRL_INSTALLATION_SPEED` | Speed used during quotes installation - multipage [1 = opening, 2 = closing] | FD | FD |  |
| 04/16 | `CTRL_OUT_OF_SERVICE_STATUS` | CU Out of service status - managed by the installer for remote maintenance the CU returns back as in service after the timing defined by the relative period | FD | FD |  |
| 04/17 | `CTRL_OUT_OF_SERVICE_PERIOD` | The period in seconds of the out of service status without any trigger | FD | FD |  |
| 04/18 | `CTRL_POSITION_OPENING` | Opening position | OK | OK | ✓ |
| 04/19 | `CTRL_POSITION_CLOSING` | Closing position | OK | OK | ✓ |
| 04/1A | `CTRL_STR_POSITION_OPEN_PERSON` | Position open person | FD | FD |  |
| 04/1B | `CTRL_PEDESTRIAN_POSITION_OPENING_1` | Pedestrian opening position 1 | FD | FD |  |
| 04/1C | `CTRL_PEDESTRIAN_POSITION_OPENING_2` | Pedestrian opening position 2 | FD | FD |  |
| 04/1D | `CTRL_PEDESTRIAN_POSITION_OPENING_3` | Pedestrian opening position 3 | FD | FD |  |
| 04/1E | `CTRL_REALIGNMENT_OPN` | Position to reach in OPEN before execute a closing during realignment | FD | FD |  |
| 04/1F | `CTRL_OPN_TOLLERANCE` | Limit switch stop tolerance for (cut off in #54) | FD | FD |  |
| 04/20 | `CTRL_STR_POSITION_INTERMEDIATE` | Position intermediate | FD | FD |  |
| 04/21 | `CTRL_INTERMEDIATE_POSITION_1` | Intermediate position 1 | OK | OK | ✓ |
| 04/22 | `CTRL_INTERMEDIATE_POSITION_2` | Intermediate position 2 | OK | OK | ✓ |
| 04/23 | `CTRL_INTERMEDIATE_POSITION_3` | Intermediate position 3 | OK | OK | ✓ |
| 04/24 | `CTRL_DECELERATION_POSITION_OPENING` | Deceleration opening position | OK | OK | ✓ |
| 04/25 | `CTRL_DECELERATION_POSITION_CLOSING` | Deceleration closing position | OK | OK | ✓ |
| 04/26 | `CTRL_DECELERATION_POSITION_INTERMEDIATE` | Deceleration intermediate position | FD | FD |  |
| 04/27 | `CTRL_INTERMEDIATE_DECELERATION_DELETE` | Delete intermediate deceleration | FD | not sent |  |
| 04/28 | `CTRL_PHASE_SHIFT_OPENING` | Opening phase shift (M2 on M1) | FD | FD |  |
| 04/29 | `CTRL_PHASE_SHIFT_CLOSING` | Closing phase shift (M1 on M2) | FD | FD |  |
| 04/2A | `CTRL_DISCHARGING_OPENING` | Discharging opening | FD | FD |  |
| 04/2B | `CTRL_DISCHARGING_CLOSING` | Discharging closing | FD | FD |  |
| 04/2C | `CTRL_MANAGEMENT_DISCHARGING_OPENING` | Management discharging opening | FD | FD |  |
| 04/2D | `CTRL_MANAGEMENT_DISCHARGING_CLOSING` | Management discharging closing | FD | FD |  |
| 04/2E | `CTRL_POSITION_RECOVERY_FOR_SENSIBLE_BORDER` | Position recovery for sensible border | FD | FD |  |
| 04/2F | `CTRL_RESET_ENCODER` | Reset encoder | FD | not sent |  |
| 04/30 | `CTRL_WORKING_WITH_2_ENGINES` | Working with 2 engines | FD | FD |  |
| 04/31 | `CTRL_QUANTITY_BRIEF_INVERSION` | Quantity brief inversion | FD | FD |  |
| 04/32 | `CTRL_INITIAL_DECELERATION_DURING_OPENING` | Initial deceleration during opening | FD | FD |  |
| 04/33 | `CTRL_INITIAL_DECELERATION_DURING_CLOSING` | Initial deceleration during closing | FD | FD |  |
| 04/34 | `CTRL_BALANCING` | Balancing | FD | FD |  |
| 04/35 | `CTRL_BRAKING_LEVEL` | Braking level | FD | FD |  |
| 04/36 | `CTRL_BRAKE_MODE` | Brake mode | FD | FD |  |
| 04/37 | `CTRL_TIME_FORCE_OPERATION` | Time force operation | FD | FD |  |
| 04/38 | `CTRL_STR_SENSIBILITY_MANAGEMENT` | Sensibility management | FD | FD |  |
| 04/39 | `CTRL_SENSITIVITY_OBSTACLE` | Obstacle sensitivity | FD | FD |  |
| 04/3A | `CTRL_SENSITIVITY_OPENING` | Opening sensitivity | FD | FD |  |
| 04/3B | `CTRL_SENSITIVITY_CLOSING` | Closing sensitivity | FD | FD |  |
| 04/3C | `CTRL_SENSITIVITY_DECELERATION` | Deceleration sensitivity | FD | FD |  |
| 04/3D | `CTRL_SENSITIVITY_DECELERATION_OPENING` | Deceleration opening sensitivity | FD | FD |  |
| 04/3E | `CTRL_SENSITIVITY_DECELERATION_CLOSING` | Deceleration closing sensitivity | FD | FD |  |
| 04/3F | `CTRL_DELETE_MAPS_IN_MEMORY` | Delete maps in memory | FD | not sent |  |
| 04/40 | `CTRL_STR_SPEED_MANAGEMENT` | Speed management | FD | FD | ✓ |
| 04/41 | `CTRL_SPEED_CRUISE` | Cruise speed | FD | FD |  |
| 04/42 | `CTRL_SPEED_OPENING` | Opening speed | OK | OK | ✓ |
| 04/43 | `CTRL_SPEED_CLOSING` | Closing speed | OK | OK | ✓ |
| 04/44 | `CTRL_SPEED_DECELERATION` | Deceleration speed | FD | FD |  |
| 04/45 | `CTRL_SPEED_DECELERATION_OPENING` | Deceleration opening speed | OK | OK | ✓ |
| 04/46 | `CTRL_SPEED_DECELERATION_CLOSING` | Deceleration closing speed | OK | OK | ✓ |
| 04/47 | `CTRL_STR_STRENGTH_MANAGEMENT` | Strength management | FD | FD |  |
| 04/48 | `CTRL_FORCE_MANAGEMENT` | Management force (mode) | FD | FD |  |
| 04/49 | `CTRL_FORCE_CRUISE` | Cruise force | FD | FD |  |
| 04/4A | `CTRL_FORCE_OPENING` | Opening force | OK | OK | ✓ |
| 04/4B | `CTRL_FORCE_CLOSING` | Closing force | OK | OK | ✓ |
| 04/4C | `CTRL_DECELERATION_FORCE` | Deceleration force | FD | FD |  |
| 04/4D | `CTRL_DECELERATION_FORCE_OPENING` | Deceleration opening force | FD | FD |  |
| 04/4E | `CTRL_DECELERATION_FORCE_CLOSING` | Deceleration closing force | FD | FD |  |
| 04/4F | `CTRL_MANUAL_FORCE` | Manual force | FD | FD |  |
| 04/50 | `CTRL_OUTPUT` | Output | FD | FD |  |
| 04/51 | `CTRL_OUTPUT_1` | Output 1 | OK | OK | ✓ |
| 04/52 | `CTRL_OUTPUT_2` | Output 2 | OK | OK | ✓ |
| 04/53 | `CTRL_OUTPUT_3` | Output 3 | FD | FD |  |
| 04/54 | `CTRL_OUTPUT_4` | Output 4 | FD | FD |  |
| 04/55 | `CTRL_OUTPUT_5` | Output 5 | FD | FD |  |
| 04/56 | `CTRL_OUTPUT_6` | Output 6 | FD | FD |  |
| 04/58 | `CTRL_TIME_SCA` | Time SCA | FD | FD |  |
| 04/59 | `CTRL_TIME_FLASH` | Time FLASH | FD | FD |  |
| 04/5A | `CTRL_TIME_ELECTRIC_LOCK` | Time eletric lock | OK | OK | ✓ |
| 04/5B | `CTRL_TIME_COURTESY_LIGHT` | Time courtesy light | OK | OK | ✓ |
| 04/5C | `CTRL_TIME_SUCTION_CUP` | Time suction cup | OK | OK | ✓ |
| 04/5D | `CTRL_MODE_TRAFFIC_LIGHT_BLUEBUS` | Mode traffic light BlueBus | FD | FD |  |
| 04/5E | `CTRL_ACCELERATION` | Acceleration | FD | FD |  |
| 04/5F | `CTRL_DECELERATION` | Deceleration | FD | FD |  |
| 04/60 | `CTRL_MODE_COMMAND` | Mode command | FD | FD |  |
| 04/61 | `CTRL_MODE_COMMAND_STEPSTEP` | Mode command STEP-STEP | OK | OK | ✓ |
| 04/62 | `CTRL_MODE_COMMAND_OPEN_PARTIAL` | Mode command PARTIAL OPEN | OK | OK | ✓ |
| 04/63 | `CTRL_MODE_COMMAND_OPEN` | Mode command OPEN | OK | OK | ✓ |
| 04/64 | `CTRL_MODE_COMMAND_CLOSE` | Mode command CLOSE | OK | OK | ✓ |
| 04/65 | `CTRL_MODE_COMMAND_STOP` | Mode command STOP | OK | OK | ✓ |
| 04/66 | `CTRL_MODE_DELAY_INVERSION_FOTO` | Mode delay inversion foto | FD | FD |  |
| 04/67 | `CTRL_MODE_DELAY_INVERSION_RIB` | Command Mode delay reverse from safety edge | FD | FD |  |
| 04/68 | `CTRL_MODE_COMMAND_PHOTO_CLOSE` | Mode command PHOTO CLOSE | OK | OK | ✓ |
| 04/69 | `CTRL_MODE_COMMAND_PHOTO_OPEN` | Mode command PHOTO OPEN | OK | OK | ✓ |
| 04/6A | `CTRL_MODE_COMMAND_PHOTO_3` | Mode command PHOTO 3 | OK | OK | ✓ |
| 04/6B | `CTRL_MODE_COMMAND_ALT_OPEN` | Mode command ALT open | OK | OK | ✓ |
| 04/6C | `CTRL_MODE_COMMAND_ALT_CLOSE` | Mode command ALT close | OK | OK | ✓ |
| 04/6D | `CTRL_MODE_COMMAND_PHOTO_1` | Mode command PHOTO 1 | FD | FD |  |
| 04/6E | `CTRL_MODE_COMMAND_ALT_PRECLOSING` | Mode command ALT pre closing | FD | FD |  |
| 04/6F | `CTRL_MODE_COMMAND_EMERGENCY` | Mode command emergency | FD | FD |  |
| 04/70 | `CTRL_STR_ENTRANCE` | Entrance | FD | FD |  |
| 04/71 | `CTRL_INPUT_1` | Input 1 | OK | OK | ✓ |
| 04/72 | `CTRL_INPUT_2` | Input 2 | OK | OK | ✓ |
| 04/73 | `CTRL_INPUT_3` | Input 3 | OK | OK | ✓ |
| 04/74 | `CTRL_INPUT_4` | Input 4 | OK | OK | ✓ |
| 04/75 | `CTRL_INPUT_AUX_TYPE` | Input AUX type | FD | FD |  |
| 04/76 | `CTRL_MODE_COMMAND_REVERSE_OBSTACLE_DURING_OPENING` | Mode command n. reverse obstacle during opening | FD | FD |  |
| 04/77 | `CTRL_MODE_COMMAND_REVERSE_OBSTACLE_DURING_CLOSING` | Mode command n. reverse obstacle during closing | FD | FD |  |
| 04/78 | `CTRL_MODE_COMMAND_REVERSE_OBSTACLE_OPEN` | Mode command REV. OBSTACLES open | OK | OK | ✓ |
| 04/79 | `CTRL_MODE_COMMAND_REVERSE_OBSTACLE_CLOSE` | Mode command REV. OBSTACLES close | OK | OK | ✓ |
| 04/7A | `CTRL_MODE_INPUT_RECLOSE_AFTER_PHOTO` | Mode input for Reclose after photo | FD | FD |  |
| 04/7B | `CTRL_MODE_INPUT_PAUSE_TIME` | Mode input for Pause time | FD | FD |  |
| 04/7C | `CTRL_INPUT_5` | Input 5 | FD | FD |  |
| 04/7D | `CTRL_INPUT_6` | Input 6 | FD | FD |  |
| 04/7F | `CTRL_BUZZER_ENABLE` | Buzzer enable | FD | FD |  |
| 04/80 | `CTRL_AUTOMATIC_CLOSE` | Automatic close | OK | OK | ✓ |
| 04/81 | `CTRL_PAUSE_TIME` | Pause time | OK | OK | ✓ |
| 04/82 | `CTRL_AUTOMATIC_WORKING_1` | Automatic working 1 | FD | FD |  |
| 04/83 | `CTRL_STR_CLOSE_AFTER_PHOTO` | Close after photo | FD | FD |  |
| 04/84 | `CTRL_RECLOSE_AFTER_PHOTO` | Reclose after photo (activation) | OK | OK | ✓ |
| 04/85 | `CTRL_RECLOSE_AFTER_PHOTO_TIME` | Time Reclose after photo | OK | OK | ✓ |
| 04/86 | `CTRL_RECLOSE_AFTER_PHOTO_MODE` | Mode Reclose after photo | OK | OK | ✓ |
| 04/87 | `CTRL_STR_CLOSE_ALWAYS` | Close Always | FD | FD |  |
| 04/88 | `CTRL_ALWAYS_CLOSE` | Always close (activation) | OK | OK | ✓ |
| 04/89 | `CTRL_ALWAYS_CLOSE_TIME` | Time Always close | OK | OK | ✓ |
| 04/8A | `CTRL_ALWAYS_CLOSE_MODE` | Mode Always close | OK | OK | ✓ |
| 04/8B | `CTRL_STR_STAND_BY` | Stand-byy | FD | FD |  |
| 04/8C | `CTRL_STANDBY_ACTIVATION` | Stand-by (activation) | OK | OK | ✓ |
| 04/8D | `CTRL_STANDBY_ACTIVATION_TIME` | Time Stand-by | OK | OK | ✓ |
| 04/8E | `CTRL_STANDBY_ACTIVATION_MODE` | Mode Stand-by | OK | OK | ✓ |
| 04/8F | `CTRL_STR_TORQUE` | Torque | FD | FD |  |
| 04/90 | `CTRL_STARTING_TORQUE` | Starting torque (activation) | OK | OK | ✓ |
| 04/91 | `CTRL_STARTING_TORQUE_TIME` | Time Starting torque | OK | OK | ✓ |
| 04/92 | `CTRL_WATER_HAMMER` | Water hammer | FD | FD |  |
| 04/93 | `CTRL_STR_PRE_FLASHING` | Pre-flashing | FD | FD |  |
| 04/94 | `CTRL_PREFLASHING` | Preflashing (activation) | OK | OK | ✓ |
| 04/95 | `CTRL_PREFLASHING_TIME_OPEN` | Time Preflashing open | OK | OK | ✓ |
| 04/96 | `CTRL_TYPE_INVERSION` | Type inversion (brief or complete) | FD | FD |  |
| 04/97 | `CTRL_COMPENSATION_SENSIBLE_BORDER` | Compensation sensible border | FD | FD |  |
| 04/98 | `CTRL_MODE_SLAVE` | Mode slave | OK | OK | ✓ |
| 04/99 | `CTRL_PREFLASHING_TIME_CLOSE` | Time Preflashing close | OK | OK | ✓ |
| 04/9A | `CTRL_BLOCK_AUTOMATISM` | Block automatism | OK | OK | ✓ |
| 04/9B | `CTRL_INTERNAL_RADIO_INH` | Internal radio switch | FD | FD |  |
| 04/9C | `CTRL_KEY_LOCK` | Keylock | OK | OK | ✓ |
| 04/9D | `CTRL_WEIGHT` | Weight | FD | FD |  |
| 04/9E | `CTRL_HEATING` | Heating | FD | FD |  |
| 04/9F | `CTRL_BURGLARY_MODE` | Anti-burglary Mode | FD | FD |  |
| 04/A0 | `CTRL_ALWAYS_INVERT` | Always Invert | FD | FD |  |
| 04/A1 | `CTRL_WIFI_MODULE_IS_PRESENT` | Wifi Module is present | FD | FD |  |
| 04/A2 | `CTRL_DECELERATIONS` | Decelerations | OK | OK | ✓ |
| 04/A3 | `CTRL_INVERT_MOVEMENT_DIRECTION` | Invert movement direction | FD | FD |  |
| 04/A4 | `CTRL_POSITION_AMPEROMETRIC_EXCLUSION` | Position of amperometric exclusion | FD | FD |  |
| 04/A5 | `CTRL_PULSES_SEGMENT_MAPPING` | Pulses per segment mapping | FD | FD |  |
| 04/A6 | `CTRL_DISABLE_CONTROL` | Disable control | FD | FD |  |
| 04/A7 | `CTRL_TIME_MAXIMUM_WORK` | Time maximum work | FD | FD |  |
| 04/A8 | `CTRL_EMERGENCY_MODE` | Emergency mode | FD | FD |  |
| 04/A9 | `CTRL_TEST_MODE` | Test mode | FD | not sent |  |
| 04/AA | `CTRL_RESERVED_1` | Reserved 1 | FD | FD |  |
| 04/AB | `CTRL_RESERVED_2` | Reserved 2 | FD | FD |  |
| 04/AC | `CTRL_MINIMUM_FREQUENCY` | Minimum frequency | FD | FD |  |
| 04/AD | `CTRL_INVERTER_MODE` | Inverter Mode | FD | FD |  |
| 04/AE | `CTRL_EMERGENCY_DECELERATION` | Emergency deceleration | FD | FD |  |
| 04/AF | `CTRL_POSITION_PHOTO_EXCLUSION` | Position of PHOTO exclusion | FD | FD |  |
| 04/B0 | `CTRL_MAINTENANCE_MANAGEMENT` | Maintenance management | OK | OK | ✓ |
| 04/B1 | `CTRL_THRESHOLD_ALARM_MAINTENANCE` | Treshold alarm mainteance | OK | OK | ✓ |
| 04/B2 | `CTRL_MAINTENANCE_MANEUVERS_COUNTER` | Maintenance maneuvers counter | OK | OK | ✓ |
| 04/B3 | `CTRL_MANEUVERS_COUNTER` | Total maneuvers counter | OK | OK | ✓ |
| 04/B4 | `CTRL_MAINTENANCE_MANEUVERS_DELETE` | Delete mainteance maneuvers | OK | not sent | ✓ |
| 04/B5 | `CTRL_SENSITIVITY_INTERVENTION_TIME` | Sensitivity Intervention Time | FD | FD |  |
| 04/B6 | `CTRL_IO_EXP_BOARD` | I/O expansion board for modular control unit | FD | FD |  |
| 04/B7 | `CTRL_MODULAR_BOARD_OPERATIONS` |  Modular control unit: european version or UL325 version | FD | FD |  |
| 04/B8 | `CTRL_RADIO_CODES_MANAGEMENT` | Radio codes management | FD | FD |  |
| 04/B9 | `CTRL_COURTESY_LIGHT_MNG` | Courtesy light | FD | FD |  |
| 04/BA | `CONTROLLER_RADIO_CODEINMEMORY` | Built in Radio code in memory | FD | FD |  |
| 04/BB | `CONTROLLER_RADIO_CFG_CODES` | Built in Radio codes configuration | FD | FD |  |
| 04/BC | `CONTROLLER_RADIO_ACTIVATION` | Built in Radio activation | FD | FD |  |
| 04/BD | `CONTROLLER_TLC_CNT` | Controller profile: Read TLC List counter (total and detailed counter) | FD | FD |  |
| 04/BE | `UNKNOWN_48644` | ? | FD | FD |  |
| 04/BF | `CONTROLLER_EDITEDCODE_BMASK` | Controller profile:  enabled radio protocol on the receiver | FD | FD |  |
| 04/C1 | `CONTROLLER_ADV_ABILITATION` | Controller profile: Advanced Abilitation, Session challenge | FD | FD |  |
| 04/CD | `CTRL_SENSITIVITY_GND_IMPACT` | Sensitivity used by Door to check the impact to ground | FD | FD |  |
| 04/CE | `CTRL_FORCE_GND_IMPACT` | Force used by Door to check the impact to ground (when mechanical blocks are not used) | FD | FD |  |
| 04/CF | `CTRL_INSTALLATION_SPEED_2` | Installation speed | FD | FD |  |
| 04/D0 | `CTRL_DIAGNOSTICS_BLUEBUS_DEVICES` | Diagnostics BlueBus devices | OK | OK | ✓ |
| 04/D1 | `CTRL_DIAGNOSTICS_INPUT_OUTPUT` | Diagnostics inputs / outputs | OK | OK | ✓ |
| 04/D2 | `CTRL_DIAGNOSTICS_HARDWARE` | Diagnostics hardware | OK | OK | ✓ |
| 04/D3 | `CTRL_DIAGNOSTICS_OTHER` | Diagnostics other | FD | FD |  |
| 04/D4 | `CTRL_DIAGNOSTICS_INVERTER` | Diagnostics inverter | FD | FD |  |
| 04/D5 | `CTRL_DIAGNOSTICS_VISUAL` | Diagnostics visual | FD | FD |  |
| 04/D6 | `CTRL_DIAGNOSTICS_MODULAR_SM_ADVLOG` | Diagnostics info for current MTB and LCU state machine used into Modular control unit | FD | FD |  |
| 04/DA | `CTRL_LOG_8_MANEUVERS` | Log last 8 maneuvers | F0 | OK | ✓ |
| 04/DB | `CTRL_ADVANCED_LOG` | Last 8 Advanced diagnostics status | FD | FD |  |
| 04/DC | `CTRL_LOG_TEST_1` | Log test 1 | FD | not sent |  |
| 04/DD | `CTRL_LOG_TEST_2` | Log test 2 | FD | not sent |  |
| 04/DE | `CTRL_LOG_TEST_3` | Log test 3 | FD | not sent |  |
| 04/DF | `CTRL_LOG_TEST_4` | Log test 4 | FD | not sent |  |
| 04/E0 | `CTRL_FORCE_AUTOMATIC_MINIMUM` | Minimum automatic force | FD | FD |  |
| 04/E1 | `CTRL_FORCE_AUTOMATIC_MAXIMUM` | Maximum automatic force | FD | FD |  |
| 04/E2 | `CTRL_FORCE_AUTOMATIC_SLOWING_DOWN_MAXIMUM` | Automatic strength for minimum slow down | FD | FD |  |
| 04/E3 | `CTRL_FORCE_AUTOMATIC_SLOWING_DOWN_MINIMUM` | Automatic strength for maximum slow down | FD | FD |  |
| 04/E4 | `CTRL_LOOP_SENSIBILITY` | Sensibility loop | FD | FD |  |
| 04/E5 | `CTRL_LOOP_CALIBRATION` | Calibration loop | FD | not sent |  |
| 04/E6 | `CTRL_LOOP_SUPPLY` | Supply loop | FD | FD |  |
| 04/E7 | `CTRL_LOOP_WORKING_ACTIVATE` | Activate loop working | FD | not sent |  |
| 04/E8 | `CTRL_LOOP_WORKING_MODE` | Mode loop working | FD | FD |  |
| 04/E9 | `CTRL_LOOP_RECALIBRATION_TIME` | Time loop recalibration | FD | not sent |  |
| 04/EA | `CTRL_LOOP_ACTIVATION_MODE` | Mode activation loop | FD | FD |  |
| 04/EB | `CTRL_LOOP_ACTIVATION_TIME` | Time activation loop | FD | FD |  |
| 04/EC | `CTRL_BURGLARY_LOOP_DETECT` | Loop Burglary function | FD | FD |  |
| 04/ED | `CTRL_LOOP_FREQUENCY_VIEW` | View frequency loop | FD | FD |  |
| 04/EE | `CTRL_PRESS_TEST` | View pressure test | FD | not sent |  |
| 04/EF | `CTRL_TEST4` | Test 4 | FD | not sent |  |
| 04/F0 | `CTRL_STR_INSTALLATION` | Installation | FD | FD |  |
| 04/F1 | `CTRL_STR_PARAMETERS_MAIN` | Main parameters | FD | FD |  |
| 04/F2 | `CTRL_STR_PARAMETERS_ADVANCED` | Advanced parameters | FD | FD |  |
| 04/F3 | `CTRL_STR_POSITIONS` | Positions | FD | FD |  |
| 04/F4 | `CTRL_STR_SECURITY` | Security | FD | FD |  |
| 04/F5 | `CTRL_STR_MAINTENANCE` | Maintenance | FD | FD |  |
| 04/F6 | `CTRL_STR_DIAGNOSTICS` | Diagnostics | FD | FD |  |
| 04/F7 | `CTRL_STR_OPTIONS` | Options | FD | FD |  |
| 04/F8 | `CTRL_STR_SETUP_INPUTS` | Inputs setup | FD | FD |  |
| 04/F9 | `CTRL_STR_SETUP_OUTPUTS` | Outputs setup | FD | FD |  |
| 04/FA | `CTRL_STR_SETUP_COMMANDS` | Commands setup | FD | FD |  |
| 04/FB | `CTRL_STR_PASSWORD` | Password | FD | FD |  |
| 04/FC | `CTRL_STR_COMPENSATION_LEVEL` | Level compensation | FD | FD |  |
| 04/FD | `CTRL_STR_LOOP_DETECTOR` | Loop detector | FD | FD |  |
| 04/FE | `CTRL_STR_INVERTER` | Inverter | FD | FD |  |
