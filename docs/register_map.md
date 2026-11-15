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
| SET | — | — | writes a value (only the `setting` entities do this) |

Addresses: the control unit is `00.03`; an OXI radio receiver is `00.0A`.

Reply status byte (`0x00` = OK):

| Status | Meaning |
| --- | --- |
| `F0` | the register has no INFO |
| `F1` | GET is not allowed (for example a command-only register) |
| `F9` | the request needs a parameter (data bytes) |
| `FD` | the controller does not support this register |

INFO layout (MyNice Pro `decompileInfo`): byte 0 = value size (bits 0-6) and
little-endian flag (bit 7), byte 1 = data format, byte 2 = function, byte 3 =
flags. Flag bit 7 adds an array size of `flags & 3` bytes. Flag bit 6 = a list
follows (a count, then the entries); with bit 5 the list holds the allowed option
codes, without it one value per field. Without bit 6: min, max, step and an
optional 2-byte scale. Flag bit 4 = divide by the scale, bit 5 (without bit 6) =
multiply by it. See `protocol/t4/info.py`.

Columns:

- **App code ✓**: read in the MyNice Pro app code.
- **Confirmed on**: the controller model on which someone saw this on a real
  gate. A meaning with neither is marked *guess*.
- **Read**: *poll* = every status poll; *extended* = the extended scan (about
  every 5 minutes); *start-up* = once per session; *not read* = documented only.

### Models that confirmed data

| Model | Firmware | Interface | Source |
| --- | --- | --- | --- |
| ROBUSR10 (NewRobus, sliding) | FG01h, hardware 604ar00 | BiDi-WiFi SB725A1, firmware 2.6.4 | #55, #57–#67 (probes, gate cycles, MyNice Pro code) |
| RBA4R10 | — | — | #14 (live `04/40` position) |
| ARIA200S / CLBOX | — | — | #11 (state only, speed encoding not verified) |
| MC824H / HYKE | CD08 | BiDi-WiFi SB725A1, firmware 2.6.4 | #38 (open: forces may be per-motor arrays) |

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

## A. Identification

### STD group 00 — controller identity (address 00.03)

The integration does **not** read these over DMP: the same values come from the
BiDi-WiFi INFO reply and fill the Home Assistant device page (see *Device
information* in entity_reference.md). They are listed so the DMP values can be
compared.

| Register | Name (app) | Example | App code | Confirmed on |
| --- | --- | --- | --- | --- |
| 00/08 | STD manufacturer | `NICE` | ✓ | ROBUSR10 FG01h |
| 00/09 | STD_PRODUCT | `ROBUSR10` (device model ID) | ✓ | ROBUSR10 FG01h |
| 00/0A | STD_HARDWARE_VERSION | `604ar00` | ✓ | ROBUSR10 FG01h |
| 00/0B | STD_FIRMWARE_VERSION | `FG01h` | ✓ | ROBUSR10 FG01h |
| 00/0C | STD description | `NewRobus` (device model) | ✓ | ROBUSR10 FG01h |
| 00/20 | STD product detail | serial-like text, not shown | ✓ | ROBUSR10 FG01h |

All of them answer `F0` to INFO (no INFO).

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
| GET with index | 9 per element | `09 03 07 81 03`: 9-byte elements, array of 3 | start-up | `controller_configuration` |

The value is an array. Element *i* is read with index *i + 1*, like MyNice Pro.
Each reply is the index byte followed by ASCII text, padded with `00`. The
integration joins the texts with `, ` and stops at the first missing element.

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
| 2 | `00`; `02` or `03` seen in some reads after a photocell reversal | — | ROBUSR10 FG01h; meaning unknown |
| 3 | `00` in every read | — | ROBUSR10 FG01h; meaning unknown |

Example: `04 ff 00 00` = open.

### 04/02 — State change event

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| live event and controller history (`T4_LOGS`); GET answers `FD` | ≥ 2 | `FD` | live | `protocol_event`, `last_state_change_cause` |

| Byte | Meaning | App code | Confirmed on |
| --- | --- | --- | --- |
| 0 | new state, same table as 04/01 byte 0 plus events such as `83` opening from command, `88` stopped for command arrival, `91` force limiter intervention | ✓ | ROBUSR10 FG01h (`05`, `83`, `88`, `91`) |
| 1 | cause, `EVT_STATE_CAUSE` table in `protocol/t4/enums.py` | ✓ | ROBUSR10 FG01h |

Seen on the ROBUSR10:

- A photocell reversal gives `83 00` (opening from command, cause *none*), not
  a photocell cause. A remote-control reversal gives no 04/02 at all.
- Cause `19` (*oview*) came with stops that Home Assistant did not send, so it
  does not mean "stopped by Home Assistant".
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
| 04/12 | maximum open position | extended | `max_open_position` | `14 73` = 5235 | ✓ | ROBUSR10 FG01h |
| 04/13 | minimum closing position | extended | `min_closing_position` | `00 00` | ✓ | ROBUSR10 FG01h (value) |
| 04/18 | open position (end of travel) | poll | position scale | `14 73` = 5235 | ✓ | ROBUSR10 FG01h |
| 04/19 | closed position (end of travel) | poll | position scale | `00 00` | ✓ | ROBUSR10 FG01h |
| 04/21 | partial open 1 position | extended | `partial_open_1_position` | `03 e8` = 1000 | ✓ | ROBUSR10 FG01h |
| 04/22 | partial open 2 position | extended | `partial_open_2_position` | `0b b8` = 3000 | ✓ | ROBUSR10 FG01h |
| 04/23 | partial open 3 position | extended | `partial_open_3_position` | `0f a0` = 4000 | ✓ | ROBUSR10 FG01h |
| 04/24 | opening slowdown position | extended | `opening_slowdown_position` | `01 f4` = 500 | ✓ | ROBUSR10 FG01h (value) |
| 04/25 | closing slowdown position | extended | `closing_slowdown_position` | `01 f4` = 500 | ✓ | ROBUSR10 FG01h (value) |

The position in percent is `(04/11 − 04/19) / (04/18 − 04/19) × 100`. On the
ROBUSR10 the open position moved by about ±100 counts between cycles (for
example 5224, 5235, 5239).

"(value)" means the value was read, but its effect on the gate was not tested.

### 04/40 — Live position

| Access | Size | INFO | Read | Entity |
| --- | --- | --- | --- | --- |
| live event only; GET answers `FD` | 2 or more | `FD` | live | `cover` position between polls |

The value format depends on the controller:

| Format | Confirmed on |
| --- | --- |
| raw encoder count; the integration divides by the open position (04/18) | ROBUSR10 FG01h (#56: 135 of 135 events within ±2 %) |
| raw scalar `0..7000` | RBA4R10 (#14) |
| percent | controllers behind a CU_WIFI interface |

See [position_calibration.md](position_calibration.md) for how each format is used.

## C. Settings

All settings are read in the extended scan. A read-only sensor or binary sensor
shows each value; the registers marked **W** also have a writable `bus_t4_*`
entity (number or switch). Before a write the integration reads the INFO of the
setting and uses its range (#58). It refuses one-value writes when INFO marks the
value as a per-motor array, and on known models it refuses writes that are not
verified (see *Write restrictions* below).

INFO strings below are from the ROBUSR10 (FG01h). Other models can give other
ranges, which is why the integration reads INFO and does not use fixed limits.

### On/off settings

INFO `01 00 01 41 02 00 01`: 1 byte, list of values `00`, `01`.

| Register | Setting | W | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- |
| 04/80 | automatic close | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/84 | close after photo | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/88 | always close | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/8C | stand-by | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/90 | starting torque | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/94 | pre-flash | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/98 | slave mode | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/9A | automation blocked | | `00` | ✓ | ROBUSR10 FG01h (value) |
| 04/9C | key lock | W | `00` | ✓ | ROBUSR10 FG01h |
| 04/A2 | slowdown | | `01` | ✓ | ROBUSR10 FG01h (value) |

### Times

Plain seconds unless a scale is given. With scale 10 the raw value is tenths of a
second; the sensor shows seconds (#60).

| Register | Setting | W | INFO (min-max, step, scale) | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/81 | pause time | W | 0-250, 1 | `1e` = 30 s | ✓ | ROBUSR10 FG01h |
| 04/85 | photo close time | W | 0-250, 1 | `05` = 5 s | ✓ | ROBUSR10 FG01h |
| 04/89 | always close time | W | 0-250, 1 | `05` = 5 s | ✓ | ROBUSR10 FG01h |
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
| 04/42 | opening speed | W | `50` = 80 % | ✓ | ROBUSR10 FG01h |
| 04/43 | closing speed | W | `46` = 70 % | ✓ | ROBUSR10 FG01h |
| 04/45 | opening slowdown speed | | `14` = 20 % | ✓ | ROBUSR10 FG01h (value) |
| 04/46 | closing slowdown speed | | `14` = 20 % | ✓ | ROBUSR10 FG01h (value) |
| 04/4A | opening force | W | `64` = 100 % | ✓ | ROBUSR10 FG01h |
| 04/4B | closing force | W | `64` = 100 % | ✓ | ROBUSR10 FG01h |

The speed in 04/D2 is a measured value, not this setting: with 80 / 70 % set,
04/D2 showed 95-99 % opening and 71-84 % closing (ROBUSR10).

### Option settings

The INFO is a list of the codes this controller allows (flags `61`). The integration
shows the code by name from the tables in `protocol/t4/options.py`; an unknown code
shows as `code_<n>`.

| Register | Setting | W | App list (INFO function) | Allowed codes (ROBUSR10) | Example (ROBUSR10) | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/51 | output 1 function | | `ListOut` (`F4`) | 00 01 03 02 04 05 17 06 23 07 0b 0f 10 11 12 | `05` flashing light | ROBUSR10 FG01h |
| 04/52 | output 2 function | | `ListOut` (`F4`) | same | `01` gate-open indicator | ROBUSR10 FG01h |
| 04/61 | step-by-step mode | | `ListCommand` (`F3`) | 01-08 | `02` open-stop-close-open | ROBUSR10 FG01h |
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
| 04/71 | input 1 function | | `ListDeviceCommand` (`F2`) | 31 codes, 00-26 | `01` step-by-step | ROBUSR10 FG01h |
| 04/72 | input 2 function | | `ListDeviceCommand` | same | `03` open | ROBUSR10 FG01h |
| 04/73 | input 3 function | | `ListDeviceCommand` | same | `04` close | ROBUSR10 FG01h |
| 04/74 | input 4 function | | `ListDeviceCommand` | 2b 2c 27 | `27` emergency stop | ROBUSR10 FG01h |
| 04/86 | photo close mode | W | `ListFunctionsMode` (`F5`) | 10 11 | `11` | ROBUSR10 FG01h (value; names not mapped) |
| 04/8A | always close mode | W | `ListFunctionsMode` | 20 21 | `20` | ROBUSR10 FG01h (value; names not mapped) |
| 04/8E | stand-by mode | | `ListFunctionsMode` | 30 31 32 | `31` safety devices | ROBUSR10 FG01h (value) |
| 04/B0 | maintenance mode | | `ListFunctionsMode` | 05 04 | `05` automatic | ROBUSR10 FG01h (value) |

"Confirmed on" means the read value matched the gate (for example output 1
flashed during every move, the photocell reversed the gate); "(value)" means only
the value was read.

### Maintenance counters

4-byte big-endian unsigned values.

| Register | Name | W | INFO (min-max, step) | Example (ROBUSR10) | App code | Confirmed on |
| --- | --- | --- | --- | --- | --- | --- |
| 04/B1 | maintenance threshold | W | 0-4294967294, 10 | `00 00 1b 58` = 7000 | ✓ | ROBUSR10 FG01h (write 7010 and back, read back each time) |
| 04/B2 | maintenance count | | 0-4294967295, 1 | `00 00 04 95` = 1173 | ✓ | ROBUSR10 FG01h (value) |
| 04/B3 | total maneuver count | | 0-4294967295, 1 | `00 00 00 22` = 34 | ✓ | ROBUSR10 FG01h (value) |

Open: on the ROBUSR10, 04/B3 (34) is lower than 04/B2 (1173), so the unit or
meaning of 04/B3 is not clear yet.

### Write restrictions

| Model | Registers | Rule | Source |
| --- | --- | --- | --- |
| ARIA200S / CLBOX | 04/42, 04/43 | writes blocked: speed encoding not verified | #11 |
| MC824H / HYKE | 04/4A, 04/4B | writes blocked unless valid INFO says the value is not a per-motor array (#63, open) | #38 |
| any | any setting | writes blocked while INFO is not loaded, and when INFO marks an array | #63 (open) |

Rules are in `models/profiles.py` and `coordinator.setting_write_block_reason`.
