# Native decoder audit

Artifact: arm64 `libnative-lib.so`, 170472 bytes, SHA256
`874d0e1d0ff486a1297b465568f49d73bf55ec411e50589e4ccecec1536a88e0`,
ELF Build ID `769d74ad8578a129c0450b6b754db9d4a107920c`.
Its bytes match the supplied Pro arm64 split APK. The library is not distributed
with this integration. Audit uses static instructions and the Java field consumers;
no vendor native code is loaded into HA or executed for this implementation.

## Audited contracts in phase 1

| Native function | Address | Verified contract | Remaining limit |
| --- | --- | --- | --- |
| T4AutomationState_init | 0x141a8 | Status byte 0; diagnostic byte 2; flags byte 1; advanced diagnostic byte 3; optional masks bytes 4/5/6 | App-reported field interpretations, not proof of current physical condition |
| decompileMenu | 0x226a4 | Two-byte flags/parameter records; group passed separately; permissions bits 7/6/5/4; group marker bit 3; indentation bits 0–2 | Mapping lookup, framing/continuation and authenticated role are not supplied by a raw record |
| T4AutomationLast8ManeuversStatus_init | 0x1609c | First eight bytes are diagnostic enum codes in vendor index order | Validity, newest-slot ordering and association with advanced history remain unverified |

Automation status uses 04/01 (`CTRL_AUTOMATION_STATUS` in the command map).
04/D3 is `CTRL_DIAGNOSTICS_OTHER`; 04/D5 is visual diagnostics; 04/D6 is the
modular state-machine advanced log. Their names do not provide those decoders.
The previously blocked diagnostic placeholders are corrected without enabling them.

The status decoder accepts the app's one-byte legacy state and optional fields
through eight bytes. It never zero-fills absent diagnostic codes or masks. Unknown
codes retain their number with a null label. Empty, all-FF and longer layouts are
unavailable. Byte 7 is emitted by native code but ignored by the supplied Java
consumer; it is retained as uninterpreted, not assigned to a guessed module.

Flags 0–4 map to Java's search-devices, search-positions, first-maneuvers,
no-programming and no-EEPROM-error booleans. Bits 5–7 are inverted by Java for PSU,
built-in-radio and modular-controller fields. That inversion is retained, not
silently corrected based on the names. An all-one flag byte occurs in existing
controller evidence; phase 1 keeps it raw and omits boolean conclusions. No
reported flag authorizes a procedure or changes the live cover state.

The native history code checks length >=7 before accessing byte 7. The integration
requires >=8 instead. Native menu code also assumes complete two-byte pairs;
odd-length input is rejected. These are deliberate bounds corrections. All-zero
maneuver slots may decode as `ok` but still do not prove eight fresh successes.

## Existing decoders and limits

INFO (`decompileInfo`, 0x22ba8), I/O and hardware helpers already have native-derived
contracts and captured regressions. Matching symbol addresses support artifact
consistency, not validation of every model or descriptor. Their existing required
INFO scaling, missing values and per-motor guards remain. Advanced history,
other/visual formats, receiver codecs and model-specific indexed writes need
additional audits and captures in subsequent phases.

Fixtures in `tests/test_native_decoders.py` are synthetic boundary and bit vectors,
not claimed hardware captures or executions of the Android code. Hardware testing
should compare the optional Controller automation state diagnostic with existing
app readouts during normal use, without learning, reset or deliberate faults.

## Reproduce artifact verification

Run `python scripts/audit_native_library.py /path/to/libnative-lib.so --pro-split
/path/to/config.arm64_v8a.apk` with your private supplied files. The output confirms
the fingerprint, audited symbol anchors and optional APK byte equality. It
does not execute code or prove dynamic behavior. The machine-readable audit scope
is [native_decoder_evidence.json](../custom_components/nice_bidiwifi/native_decoder_evidence.json).

## Phase 2 menu and session audit

`RxJavaT4Manager.getControlUnitMenuCommands` requests STD_MENU with a group-4
selector. `getCommands` uses reply FIN to end the query and otherwise resubmits
the reply sequence. The bounded reader starts at sequence zero, validates the
controller source and both checksums, rejects ambiguous/missing pages, cyclic
cookies, odd records and oversized/incomplete results, and yields to queued work.
No partial result can become a permission snapshot.

Native DMPGet at 0x23694 extracts five header fields in order: group, command,
flags, **sequence**, status. It does not identify the sequence as a value length.
T4Reply_getSeq at 0xd074 consumes the fourth field. T4Reply_getFinishFlag at
0xcefc returns the flag byte and Java checks its low bit. Request framing at
0x238b8 places the sequence before the request payload length. These anchors
justify the paged STD_MENU request and the correction to the general reply parser;
DNP frame lengths and checksums still validate payload integrity.

The supplied Pro path selects installer GET/SET columns when building its menu
model. No recovered application call establishes that HA's NHK principal is an
authenticated controller installer. Native STD_FUNCTION_AUTHORIZATION and password
symbols do not establish the missing exchange, response layout or role lifetime.
No password or authorization-register probe is added to guess that contract.

## Pro controls correction in b17

The b16 `ControllerRoleProof` requirement was an integration restriction, not an
established device requirement. Follow-up review of LocalControlUnitsTabPresenter
shows its security dialog compares the input PIN with the app-cached ControlUnit
PIN before opening the installed controller. LocalControlUnitsProtectionPresenter
reads/changes the password for its protection screen. Neither observed comparison
produces a role token consumed by the reviewed normal parameter SET path.
RxJavaT4Manager selects installer columns for its controller menu and sends normal
settings directly through the authenticated connection.

B17 follows that path with explicit HA Pro controls selection. It does not read,
change or export the controller PIN, and does not claim a device role. Fresh module
admin, complete current menu and INFO, exact target/model/session, scalar format,
ranges/options, stationary state and one-send/readback remain enforced. Controller
rejections are surfaced and never bypassed or automatically replayed.

Actual password/authorization exchanges for other device paths, unknown parameters,
indexed motor identities and installer/receiver procedures still need their own
verified contracts. Pro controls does not invent those missing bindings.

## Phase 3 receiver audit, first coherent set

The earlier research map extractor mishandled destination/source register numbers
in ADD/OR instructions. Correcting those static dataflow operations recovers the
radio output-status binding 0A/08 and transmitter-counter binding 0A/70, matching
the Java RadioOutputStatusResult and RadioReceiverTransmittersCounters consumers.
It also identifies 0A/6F challenge, 0A/71 supported and 0A/72 enabled-protocol fields.
These corrections do not automatically activate every recovered map entry.

RadioOutputStatusResult_parseResponse at 0x15870 reads five Java shorts holding
five wire bytes: flags, two big-endian timer words. They are reported channel
flags, not proof of physical relay state; timer units are not guessed.
RadioReceiverTransmittersCounters_parseResponse at 0x175b8 copies bounded data;
Java consumes a big-endian total followed by protocol/u16-count triples. The
integration rejects incomplete or duplicate triples, all-FF filler and excess
length rather than converting invalid input to zero. Protocol totals are not
assumed to sum to the overall total.

The protocol wrapper at 0x174fc accepts 12 bytes. The Java consumer unscrambles
and XORs eight bitmap bytes using a RadioAuthentication context, then checks the
little-endian additive signature. This is the manufacturer's checksum scheme,
not a modern MAC/HMAC. A pure codec implements the recovered nibble transform,
context XOR and command-specific signature. Independent vectors cover context
mismatch and tampering. Encrypted bytes are never presented as plain capabilities.
The passive inspector does not start a challenge SET or claim that those masks
were inspected. The scoped challenge exchange is the next runtime contract.

The eight/ten-byte remote-record codec follows the Java record constructor.
Remote identity codes and rolling counters are private fields and absent from
its public summary. It is not an authorization to scan or export receiver memory.
Live memory queries, paging, credentials and mutation completion/recovery remain
separate parts of the final phase.

The passive reader verifies radio profile 0A at the existing 00.0A bus slot before
reading model/version, type/coding, outputs and counts. It never reads a PIN, key,
certificate or memory-code register. Unsupported optional fields remain explicit;
missing data is not a count of zero. Model identity is not serial identity and
these observations never grant mutation authority.
