# Installer workflows and device lifecycle

Phase 6 provides the procedure runner and the source-backed shared-NHK surface below. A documented blocked procedure is not a claim that the app function works in HA. No new installer operation runs automatically after upgrade.

## Available operations

Configure > Installer procedures and device lifecycle opens the explicit workflow selector. Inspect advertised devices reads NHK INFO, preserving reported interface/device IDs and bounded model/hardware/firmware fields. It does not claim a complete BusT4 or BlueBus scan and retains no serials, remote codes or personal names.

Inspect existing bootloader state requires a local, stationary shared-Wi-Fi interface and verified administrator access. It reads FILE_INFO, DEV_ON_BOOT and STATUS_PROGRAMMING using T4_UPDATE/BOOT_1 and the interface ID advertised in fresh INFO. It never calls START_BOOT, FILE_ERASE, FILE_WRITE, START_PROGRAMMING or STOP_PROGRAMMING. Unsupported queries remain errors, not empty successful inventories. Observed progress keeps its reported integer and an unverified unit; it is not presented as a percentage. CRC information does not authenticate a firmware image. Partial observations have individual capture times and are cleared before a new attempt.

Snapshot observed configuration captures only already observed, allowlisted scalar configuration values and their source/time. It performs no extra scan and omits password, radio-memory, unknown and indexed-motor payloads. Diagnostics include the snapshot and comparison with the previous same-profile snapshot. This is incomplete, non-restorable evidence, not an app backup or authorization for controller transfer. There is no endpoint that replays it into the controller.

The existing module-reboot button and new explicit workflow share one restart procedure. Its result establishes CHANGE acknowledgement only; a new connection cannot independently prove that the physical module rebooted. It preserves the existing post-request session release and reconnect behavior. Restart is distinct from factory, Wi-Fi, controller and receiver reset.

Optional disabled-by-default Installer procedure and Advertised devices sensors expose procedure state and the last observed device count. Enabling them does not start a scan, learning or update. The manufacturer app remains required for blocked workflows.

## Ownership, cancellation and recovery

All mandatory handlers, prerequisites, completion and recovery are validated before any preparation, storage or network work. Mutations additionally require explicit intent, fresh module administrator evidence, a stationary gate, model/capability checks and an unchanged device session at dispatch. Module administrator is not installer-menu permission. Controller programming is separate from HA animation calibration; position learning is not assumed to reset force adaptation.

Only one procedure owns the runtime at a time. A submitted installer mutation blocks other configuration/access writes and new gate movement; Stop remains allowed. An explicitly observed BOOT_1 RUN status also blocks administration/new movement in the current runtime until a definitive fresh status; a failed or unknown inspection cannot clear that observation. No firmware probe runs automatically. Deadlines are per step. Cancellation of an await does not stop a network worker: the runner drains it before releasing ownership, records uncertain delivery and never replays a mutation. User cancellation is a request at a supported boundary; no guessed abort command is sent for an irreversible submitted step.

Before dispatch, mutation intent is saved in HA storage under an entry-scoped procedure journal. No credentials, images or raw radio memory are stored. A restarted HA sees an interrupted dispatched step as needs_recovery, never as instructions to resume it. Corrupt/unreadable journals also block further installer mutations. Ordinary gate monitoring/control remains available after an interrupted restart once connectivity works; the pending installer mutation hold is not silently discarded.

The recovery screen only clears an interface-restart hold after explicit operator confirmation, fresh administrator inspection and device metadata. It records acknowledgement of an unresolved outcome, not fake success, and persists before releasing the hold. Other procedures require their complete model-specific recovery adapter. Read-only inspection cannot erase a pending mutation journal.

## Procedure and gap matrix

| Procedure | Status | Mandatory steps | Prerequisites | Gaps |
| --- | --- | --- | --- | --- |
| `inspect_devices` | implemented | read_info | configured access | none |
| `inspect_boot` | implemented | read_info, file_info, boot_devices, programming_status | administrator, stationary, local, advertised_interface_id | none |
| `snapshot_configuration` | implemented | capture_observations | configured access | none |
| `restart_interface` | implemented | send_restart_once | administrator, stationary, reboot_capability | none |
| `discover_bus` | implemented | profiles_and_public_identity | administrator, stationary | none |
| `search_devices` | blocked | validate_model_sequence, start_search, observe_completion | installer, stationary, physical_setup | installer.commissioning |
| `search_bluebus` | blocked | validate_model_sequence, start_bluebus_search, observe_completion | installer, stationary, physical_setup | installer.commissioning |
| `learn_positions` | blocked | validate_motor_ownership, model_learning_sequence, verify_endpoints | installer, physical_setup, gate_visible | installer.commissioning |
| `learn_force_map` | blocked | validate_fg01h, learn_force_map, verify_adaptation | installer, physical_setup | fg01h.force_map_learning |
| `radio_inspect` | implemented | read_redacted_page | administrator, stationary, pro_controls | none |
| `radio_enroll` | blocked | physical_consent, receiver_mode, write_remote, verify_remote | installer, receiver_adapter, physical_remote | installer.radio_mutation |
| `radio_edit` | blocked | read_target, authorize_channel, write_once, readback | installer, receiver_adapter | installer.radio_mutation |
| `radio_remove` | blocked | identify_exact_remote, remove_once, verify_removal | installer, receiver_adapter | installer.radio_mutation |
| `radio_reset` | blocked | verify_receiver_scope, reset_once, verify_receiver_recovery | installer, receiver_adapter | installer.radio_mutation |
| `backup_configuration` | blocked | native_snapshot, integrity_and_ownership, backup_manifest | installer_adapter | installer.backup_restore |
| `restore_configuration` | blocked | validate_manifest_and_target, ordered_restore, verify_and_recover | installer, physical_setup | installer.backup_restore |
| `replace_controller` | blocked | motor_module_state, logic_module_state, rebind_target, verify_replacement | installer, physical_setup | installer.replacement |
| `reset_controller` | blocked | validate_reset_semantics, reset_once, verify_recovery | installer, physical_setup | installer.reset |
| `reset_interface` | blocked | verify_ownership, reset_once, reprovision_and_repair | administrator, physical_setup | installer.reset |
| `firmware_catalog` | blocked | authorize_catalog, match_board_and_model, verify_provenance | vendor_account | installer.firmware_catalog |
| `update_interface_firmware` | blocked | validate_vendor_image, verify_local_target, upload_chunks, verify_version_and_recover | administrator, local, stable_power | installer.interface_firmware |
| `update_controller_firmware` | blocked | validate_vendor_image, stage_file, enter_boot, program, verify_and_recover | installer, local, stable_power | installer.controller_firmware |

## Firmware, receiver and installer adapter boundaries

Module HTTP OTA and controller BOOT_1 programming are different protocols. The recovered module helper uses 1024-byte HTTP chunks at /wiced_ota_server/upgrade_chunk.html. Copying its chunk retries would not establish safe reconciliation after uncertain delivery. Controller programming requires separate staging, exact board/motor ownership, boot transitions and final verification. FirmwareArtifact checks model/hardware/protocol and byte length/hash; the firmware adapter still rejects installation until vendor authenticity, compatibility and full recovery are verified. A correct SHA-256 or device-reported CRC is not proof of vendor authenticity. No image URL/password is collected and no firmware download, erase or upload occurs in this beta.

Receiver T4_RADIO_RD/WR envelopes alone do not establish native memory rows, certificates, rolling codes, receiver identity or channel authorization. Their raw data is never probed or retained. NHK paired-user approval is a separate feature from physical remote enrollment. Existing BlueBus diagnostic readings are separate from programming a new device search.

Additional BusT4 installer, receiver, CORE, ProView and phone NFC paths have explicit adapter/gap boundaries. A phone-only API is not automatically available through the existing NHK transport. Controller replacement needs the recovered motor-then-logic sequence plus native status decoding and T4Source rebinding. CTRL_RESET in the recovered ListDeviceCommand may clear a previous command; its name cannot authorize a factory reset.

Source basis: Pro 2.18 FirmwareManagerImpl, T4UpdateBoot1Requests, FwInterfaceRequest/Response, FwDevice, T4UpdateStatus, T4DiscoveryProcedure, InstallationProcedure/InstallationFinder, receiver procedures and InterfaceFirmwareUpgradeHelper. The source index refines reviewed roots to these semantic destinations without treating app enum ordinals as wire codes. An arm64 native library is now available for audit; exact version association, complete installation model assets, authenticated firmware images and recovery contracts remain unverified. Other screen/domain entries retain their initial inventory routing for phase 8 closure.

## Beta checks

Update and restart HA; preserve current calibration and controller programming. Check ordinary controls, position, parameters and connection recovery. Use advertised-device inspection and configuration snapshots, compare their fields/counts with the app and download sanitized diagnostics. Bootloader inspection is optional on a suitable stationary test installation with other apps closed; an unsupported response is useful evidence and must not trigger writes. Do not use a production gate to test blocked learning, reset, enrollment, restore or firmware operations. No physical mutation was performed during implementation; active workflows still need appropriate hardware validation.
