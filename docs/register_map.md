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
