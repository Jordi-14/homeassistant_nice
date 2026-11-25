# Final-phase capability audit

The signed Pro 2.18 base APK and both arm64 splits pass Google's APK v2/v3
verification with the same certificate. The native library is identical to the
supplied artifact. Decompiled vendor source, APKs and native code are not shipped.
See [native audit](native_decoder_audit.md) for fingerprints and contract anchors.

## Working inspection paths

- Pro mode exposes 35 additional controller editors when complete menu and INFO
  are present. All 35 were readable on SLIGHTR10 / 604a1r00 / FG01h, with existing
  values preserved and no parameter SET sent by installation or discovery.
- Addressed receiver inspection validates profile 0A at 00.0A, identity/version,
  coding and optional telemetry. OXIBD / 672B / BK00E reports output/count queries
  unsupported. This is a device response, not a count of zero or a broken session.
- Stored-slot inspection uses bounded 0A/25 GETs, verifies exact position echoes
  and strict record lengths, then discards remote codes and rolling counters.
  The standard installer inspection shares the same public summaries. A page is
  not a complete inventory or a restorable backup.
- Built-in controller-radio capabilities use 04/C1/BE/BF on controller 00.03,
  distinct from external OXI memory/status reads. Signed capabilities use explicit
  nonce transactions and command-specific
  integrity checks. Unknown delivery never causes a retry or route fallback.
  They do not program remotes or change enabled protocols. Hardware acceptance
  remains separately reported, including an explicit unsupported response.

## Remaining completion points

| Capability | Recovered evidence | Required before enabling execution | Existing extension point |
| --- | --- | --- | --- |
| Controller device / BlueBus search | DevicesScanPresenter selects the menu-supported search, SETs 1, polls GET until zero, may clear position search with SET 0, then checks automation state | Exact model/menu contract, meaningful completion state and supervised recovery/physical comparison | InstallerProcedure + LifecycleRuntime handler, guarded single-send transport |
| Controller position learning | AutomaticQuotesPresenter SETs position search to 1, polls the search/status path, and sends 0 on explicit stop; manual/motor/first-maneuver screens differ by model | Vendor catalog or updated procedure mapping for the exact product, motor ownership and supervised endpoint/abort tests | Model-specific InstallerProcedure handlers and ProcedureRunner journal |
| FG01h force adaptation | Separate force/learning symbols; position search exists | A documented independent start/progress/terminal/abort contract; do not reset learned force by guessing a position-search opcode | FORCE_MAP_PROCEDURE remains blocked |
| Edit, remove or enroll remotes | BaseRemotes and RadioSetCodeProcedure identify slot readback and 8/10-byte private records; native deletion emits position plus zero record, not position alone | Exact receiver record variant, selected physical remote/target, credential requirements, explicit intent journal, single-send reconciliation and supervised validation | Separate receiver mutation procedure handlers; inspection grants no mutation authority |
| Receiver reset | RadioReceiverResetProcedure sends RADIO_CLEAN and observes slot zero; app uses a seven-second wait | Target-specific data-loss semantics and reliable terminal/recovery evidence; a delay or one empty slot alone is insufficient | InstallerProcedure radio_reset and durable recovery journal |
| Complete backup / restore | Recovered Backup.restoreFromBackup includes reset, settings/PIN, keys, certificates, remotes, bidirectional bindings and alias | Compatible private sample archive, per-target record variants, ownership and secret handling, complete manifest and interruption/recovery tests | Installer backup/restore contracts; configuration snapshots remain explicitly non-restorable |
| Board replacement | T4ResetToSparePartsProcedure resets motor then logic, can change T4Source and performs menu-dependent search | Exact module identities, model sequence, rebinding and interrupted-transfer recovery | replace_controller procedure contract |
| Wi-Fi factory/configuration reset | Distinct NHK reset commands and affected scopes are recovered | Complete target/ownership and operational re-provisioning/re-pairing path | reset_interface procedure contract and provisioning adapter |
| Vendor firmware catalog | Pro NiceApi search/list/download paths and NiceAuthorization session; public search returns 401 | A valid authorized Pro cloud session and exact model/hardware eligibility from vendor metadata | FirmwareArtifact / FirmwareAdapter and firmware_catalog procedure |
| Controller firmware | Recovered FirmwareManagerImpl stages 1024-byte FILE_WRITE chunks and BOOT_1 state/programming commands | Compatible authenticated vendor image and metadata, exact target/board mapping, CRC/status checks and power/interruption recovery validation | FirmwareAdapter and inspect_boot observations |
| Interface firmware | App uses the separate local WICED OTA chunk endpoint | Compatible vendor image, target identity, acknowledged offsets and interrupted OTA recovery | Interface firmware adapter slot; never substitute BOOT_1 |

The bundled installation file contains 15 model groups. InstallationFinder first
matches the catalog product code and then the scan product. SLIGHTR10 is absent;
SLIGHT is present, but similarity is not a verified alias. An updated vendor
mapping or matching app capture can fill this completion point without rewriting
the transaction, target-scope and recovery foundations.

Unknown procedure fields remain explicit unavailable bindings. Replacing a missing
binding also requires its layout, target, completion and interruption fixtures.
Neither a native symbol nor a menu entry on its own establishes a complete physical
procedure. No reset, learning, enrollment, removal, restore or firmware operation
is performed automatically during upgrade, discovery or an inspection test.
