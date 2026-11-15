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
