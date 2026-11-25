# Native decoder audit

Artifact: arm64 `libnative-lib.so`, 170472 bytes, SHA256
`874d0e1d0ff486a1297b465568f49d73bf55ec411e50589e4ccecec1536a88e0`,
ELF Build ID `769d74ad8578a129c0450b6b754db9d4a107920c`.
Its bytes match the supplied Pro arm64 split APK and the complete Pro 2.18 archive. The library is not distributed
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

## Scoped receiver memory and capability inspection

The bounded memory query uses RADIO_CODEINMEMORY 0A/25 with a two-byte big-endian
position. Native qryPosition at 0x1482c and the BaseRemotes.readLegacyCode caller
agree on this shape. A response must contain the exact position echo plus an
8/10-byte record; short, wrong-position or oversized replies are not a valid page.
The operator page is limited to 16 slots, and only redacted summaries leave the
client. It is never described as a complete receiver backup or inventory.

The authentication codec is now wired as an explicit operator inspection. For
the existing external radio slot it verifies profile 0A, reads the four-byte T4
identity privately, sends a fresh four-byte client nonce to the audited 0A/6F
challenge register, reads the receiver nonce and supplies the command-specific
request signature before decoding each signed mask. The app's certificate-zero
context is used for this reviewed inspection path, not a guessed PIN or key.
Fresh challenges are used for supported/enabled reads separately. No identity,
nonce, certificate or encrypted body is retained in the public result.

This exchange changes ephemeral authentication state, not enabled radio formats or
gate configuration. It therefore uses a single-send route with no implicit cloud
fallback/replay after uncertain delivery. Failed authentication or a mismatched
signature is an error, never an empty capability list. A receiver that does not
support this extension remains explicitly unsupported; there is no key probing.

## Complete app archive verification

The complete Pro base APK identifies version v2.18 r188 / 20261008. Google's
ApkVerifier validates its v2/v3 signatures and those of both the downloaded arm64
split and the originally supplied arm64 split. All three report signer certificate
SHA256 `4d3ee4c411e18c709d66b456ddabd685bd0a847485d748f61d04c7571f1ad753`.
The contained native library matches the fingerprint at the top of this document.
These checks establish consistency with the supplied signed artifact; no app or
vendor native code was executed. Neither APK nor decompiled source is distributed.

InstallationFinder's recovered body first matches the control-unit catalog code,
then falls back to the reported scan product. It loads a local updated procedure
file, falling back to bundled `res/raw/installazione.json`. That bundled file has
15 model groups with ordered screen identifiers. It is not a generic opcode list:
motor selection, manual endpoint gathering and first maneuvers vary by group.
The literal `SLIGHTR10` is absent from this bundled table. A `SLIGHT` group exists,
but substituting it without the catalog mapping is not verified model support.

## Live b19 inspection

On SLIGHTR10 / 604a1r00 / FG01h, all 35 additional Pro editors acquired complete
metadata and readable values. The six-page controller menu contains 82 records.
The existing 00.0A slot advertises OXIBD group / 672B / BK00E, receiver type OXI,
coding Flor. This firmware returns FD (unsupported) for 0A/08 and 0A/70, so output
status and transmitter counts remain explicitly unsupported rather than zero.
No controller parameter or stored remote was changed for these checks.

B20 renews menu evidence at ten seconds, leaving margin before its thirty-second
expiry. Permission renewal uses READBACK priority above optional diagnostics;
STOP and CONTROL still take precedence and paging remains preemptible. A failed
replacement still revokes the permission snapshot. Bounded memory inspection is
also available through the existing installer lifecycle with shared redaction.


## B21 correction: controller radio and external receiver are distinct

The b20 runtime incorrectly applied external map symbols 0A/6F/71/72 to the
reviewed challenge recipe. A deeper caller review establishes that
Configuration.ProtocolSettingKey uses CuProtocolsHandler, whose supported/enabled
queries are CONTROLLER_CODE_BMASK and CONTROLLER_EDITEDCODE_BMASK. ReceiverManager
uses CONTROLLER_ADV_ABILITATION. These bind to 04/C1, 04/BE and 04/BF on the
controller proxy, not 0A/6F/71/72 on the external OXI target.

B21 removes those external authentication request bindings from the runtime
allowlist. Built-in-radio inspection addresses controller 00.03, verifies profile
04, checks C1/BE/BF INFO when implemented and requires a successful four-byte
challenge GET before any nonce SET. Explicit INFO-not-implemented F0 can use the
fingerprinted app's fixed layouts; command-not-found FD cannot. Nonces and MAC
remain private, each query has its own challenge, and no uncertain SET is replayed.
The app's getDeviceT4MAC consumes the first four STD_MAC bytes; b20's exact-length
requirement was also unnecessarily restrictive and is corrected with bounds.

The b20 live slot and capability attempts returned generic UI failures. Ordinary
local gate control and all 35 Pro editors remained healthy afterward, with no
controller setting or stored remote changed. B21 records sanitized error type,
code/register and stage while excluding arbitrary exception text, and never
exports an old-generation operator result as a current inspection. These changes
allow the subsequent hardware attempts to establish actual supported scope.

## Answered receiver data is distinct from a failed transport

A valid addressed receiver reply with an unverified memory layout must not be
reported as a broken TLS session. The bounded reader maps record length/position
contract failures to NiceReceiverFormatError, retains numeric lengths/header
metadata only, and leaves the connection established. Such a response does not
trigger a reconnect, replay or cloud-route fallback. Pure codecs still reject the
invalid record. Sanitized cause-chain extraction preserves the original stage
when a transport wrapper has retained it only through its exception cause.

## Read-only BusT4 profile discovery

T4SourceExtensions defines the app's default controller as 00.03 and broadcast
as FF.FF. RxJavaT4Manager.sendBroadcastCommand sends ordinary GET; the discovery
procedure first requests STD_PROFILE, then addresses the sources that replied.
The integration bounds the broadcast batch to 64 replies and validates DNP
checksums, addressed source, group/parameter, operation and leading profile byte, matching the app's firstOrNull consumer.
Additional bytes are not interpreted.
It rejects conflicting profiles for one source. Unknown profile codes remain
numeric and do not imply a supported HA actuator.

Only the first eight observed sources receive bounded STD_PRODUCT, STD_VERSIONHW
and STD_VERSIONFW GETs, with receivers/controllers prioritized. The reader never
queries descriptions, MACs, PINs, keys or private memory during discovery. It yields
to higher-priority work and fences the result to the current session. An inspection
is a partial observation: absent devices and complete inventory are not asserted.
A profile, bus address or model string never creates write permission.

The controller inventory generator now retains seven individually audited native
radio bindings, including 04/BE and 0A/08/25/6F/70/71/72. This removes obsolete
missing-binding placeholders without enabling generic GET/SET access. Structured,
private and procedure fields still require their dedicated scoped adapters; a
known map entry is not a runtime transaction or permission grant.


## OXIBD BK00E slot-query reply kind

A b22 inspection captured only the safe response envelope: source 00.0A,
register 0A/25, flags 01, sequence 10, status 00 and total frame size 26. Private
payload bytes were neither exported nor retained. The earlier GET-operation
matcher discarded this reply kind before the memory decoder could inspect it.

B23 accepts a FIN-only 01 reply solely for the addressed 0A/25 GET with its exact
two-byte position query, in addition to an ordinary GET reply. Other operations,
registers and targets do not receive this exception. The same DNP lengths, both
checksums, final flag, zero status, record shape and exact position echo still
apply. A known reply kind does not grant SET access or bypass private-code
redaction. The native QueryMemoryResult anchor is included in the fingerprinted
audit. Real slot decoding is a separate subsequent hardware check.

The corrected built-in capability inspection on SLIGHTR10 reports FD at 04/C1.
That is an explicit unsupported command. It stops before any nonce SET and keeps
the existing local connection and controller settings available.
