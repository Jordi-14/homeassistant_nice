# Parity closure and compatibility limits

Phase 8 reconciles the supplied Welcome 1.3.1 and Pro 2.18 r188 evidence to the approved integration boundary. It closes the software/inventory plan with explicit supported subsets and tested blockers; it does not assert universal hardware support or enable every app feature.

There are 3939 indexed evidence items, including models/utilities and generated references that are not standalone capabilities. The final machine-readable record for every source is [parity_inventory.json](../custom_components/nice_bidiwifi/parity_inventory.json). Original IDs, relative paths and SHA-256 are preserved. The source-backed build verifies all indexed file hashes. Static package/family and known-entry-point reconciliation is distinguished from runtime and physical validation; a source class can serve several operations and its entire method set is not claimed implemented.

No generic planned or review_required source disposition is accepted by the closure generator. The index is documentation only and never drives device dispatch. Supported subsets still obey model, INFO format, role, field ownership, session and procedure checks. App enum ordinals are not wire addresses. HA-native software and manufacturer-app ownership/service functions remain deliberate boundaries, not missing duplicate implementations.

## Evidence dispositions

| Disposition | Source items | Meaning |
| --- | ---: | --- |
| app_prototype | 54 | App dummy/demo flow, not a verified operational protocol |
| blocked | 822 | Named gap and negative execution boundary |
| external_dependency | 394 | A host/hardware/transport dependency remains unavailable |
| generated_reference | 2 | Resource/build constant retained only for provenance |
| ha_native | 458 | Use native HA/Companion UI or software; no duplicate engine |
| implemented | 44 | Named reviewed action/setting path exists; device support/permissions remain separate |
| implemented_read_only | 8 | Scoped inspection path; no resource execution |
| manufacturer_app | 543 | Use the manufacturer app for vendor-only workflows/resources |
| non_capability | 1 | Sentinel or undefined enum item |
| privacy_protected | 9 | Secret/security material deliberately excluded from generic probe/export |
| supported_read_blocked_write | 22 | Installer/menu permission evidence is missing |
| supported_read_only | 111 | Read only with complete validated metadata |
| supported_subset | 843 | Named runtime family exists with explicit additional gaps |
| supporting_evidence | 628 | Shared model/utility/container, not a standalone capability |

## Capability families and extension boundaries

| Family | Disposition | Existing feature links | Blocking evidence | Boundary |
| --- | --- | --- | --- | --- |
| controller | supported_subset | controller.configuration | controller.menu_wire, controller.index_identity, controller.binding_map | ParameterRepository.prepare_write / ControllerConfiguration.view |
| diagnostics | supported_subset | diagnostics.complete | diagnostics.automation_structure, diagnostics.receiver_layouts | DiagnosticRuntime.view / async_read |
| module_access | supported_subset | setup.access | runtime.connection.code1, setup.ownership_removal, setup.cloud_registration | ConfigFlow pairing / AccessManagement / client change_user |
| wifi_provisioning | blocked | setup.access | setup.wnc_recovery | ProvisioningMutationAdapter.configure |
| core | supported_subset | core.tables, transport.core | transport.core, ecosystem.core_execution | CoreTableReader / generic CORE actuation remains blocked |
| receiver | blocked | installer.radio_inspect, installer.radio_enroll, installer.radio_edit | installer.radio_memory, installer.radio_mutation | InstallerProcedure.preflight / receiver adapter boundary |
| commissioning | blocked | installer.search_devices, installer.search_bluebus, installer.learn_positions | installer.commissioning, controller.index_identity | InstallerProcedure.preflight / ProcedureRunner |
| firmware | supported_subset | installer.inspect_boot, installer.firmware_catalog, installer.update_controller_firmware | installer.firmware_catalog, installer.controller_firmware, installer.interface_firmware | FirmwareAdapter.validate_vendor_image / installer preflight |
| backup | blocked | installer.backup_configuration, installer.restore_configuration, installer.replace_controller | installer.backup_restore, installer.replacement | InstallerProcedure.preflight |
| reset | blocked | installer.reset_controller, installer.reset_interface, installer.radio_reset | installer.reset, installer.radio_mutation | InstallerProcedure.preflight |
| host_adapter | external_dependency | transport.proview, installer.discover_bus | installer.host_adapter, transport.proview, installer.bus_discovery | BlockedAccessAdapter / installer adapter preflight |
| device_categories | blocked | ecosystem.device_adapters | ecosystem.device_adapter | CategoryAdapter / require_gate_adapter |
| ha_organization | ha_native | ecosystem.areas, ecosystem.dashboards | none by design | FeatureDefinition.require_implemented deliberate HA disposition |
| ha_automation | ha_native | ecosystem.scenes, ecosystem.schedules, ecosystem.notifications | none by design | FeatureDefinition.require_implemented deliberate HA disposition |
| vendor_account | manufacturer_app | ecosystem.sharing, ecosystem.service_linking | none by design | FeatureDefinition.require_implemented manufacturer disposition |
| vendor_resources | manufacturer_app | ecosystem.vendor_resources | none by design | require_inspection rejects resource operations |
| cloud_inspection | implemented_read_only | ecosystem.inspection, ecosystem.report | none by design | NiceCloudAutomationInspector fixed GET allowlist |
| app_prototype | app_prototype | parity.report | none by design | Evidence only; no operation is registered |
| app_catalog | manufacturer_app | parity.report | none by design | Evidence only; no operation is registered |
| phone_presentation | ha_native | ecosystem.dashboards, ecosystem.permissions | none by design | Evidence only; no Android UI/permission engine is installed |
| shared_evidence | supporting_evidence | parity.report | none by design | Evidence index is never used for dispatch |

## Source audit corrections

CORE protocol classes had been indexed as supporting classes. Their Java bodies show CoreProtocol inheriting NHKProtocolImpl and fixed TABLE_READ requests. The new reader implements rule/scene counts with table-version bracketing, rejects other endpoints/table names, and never sends table writes, execution, binding or gate-status probes. Private rows, names, RF codes, action lists and version strings are not exported. The generic CORE actuator profile remains unavailable; table content does not establish active execution, timezone, per-device mapping or all category-specific control/state semantics.

Some Welcome alarm/configuration screens explicitly use DummyFlowsManager, DummyAlarmStatus and dummy initialization lists. They are recorded as app prototypes rather than fabricated alarm entities or claims that an operational alarm protocol is missing from this integration. Model/DTO support rows inherit the reviewed parent-flow disposition. Android resources/navigation/hardware surfaces are likewise separated from actual device capabilities.

A live b10 read-only cloud test successfully authenticated and matched the accessory, but the collections were unrecognized despite the user reporting no configured resources. Raw responses had intentionally not been retained. B11 recognizes explicit successful null collections according to the SDK/repository contract; omission, error replies and mismatched scope remain unknown. Completely unknown replies now produce a failed inspection and an explicit collection error. Safe fixed-field shape evidence contains only types/presence/result flags, never arbitrary keys, values or raw bodies, so a subsequent read can resolve remaining schema differences without sharing secrets. A failed attempt preserves the original time of older valid data. This is not a claim that the live empty reply has already been fully validated on b11.

## Explicit CORE inspection

For a separately identified CORE with existing approved NHK credentials, close competing app sessions and use `python scripts/inspect_core_tables.py --host CORE_IP --credentials PRIVATE_JSON --output NEW_REPORT.json`. The private JSON fields are username, password_hex (or password), target_mac and optional source_id. No account login, pairing, credentials on the command line, HA entry creation or resource import occurs. The report is count-only and has no raw/sensitive export flag. Use the known local endpoint and the same local TLS/NHK compatibility constraints documented for the integration. No CORE hardware was available during this implementation, so this path is source/fixture tested and needs read-only hardware beta validation.

An already loaded CORE entry can request the same reader from Configure. Non-CORE configured entries are rejected before any table query. The tool does not make CORE a supported generic gate/actuator profile. Missing versions, continuation, unknown layouts or changes during the read reject the snapshot instead of displaying incoherent or false-zero counts.

## Compatibility and real-device limits

| Matrix case | Evidence level | Scope | Remaining limit |
| --- | --- | --- | --- |
| upgrade_identity | software_fixture | Legacy/current entry migration, credential and entity-ID preservation, unload/reload fences | No registry migration is introduced by b11; fixtures do not establish every historical custom entity name. |
| shared_wifi_routes | software_fixture | BiDi-WiFi, IT4WIFI, CU_WIFI and local/cloud/fallback serialization, no automatic write replay | All firmware/network variants have not been physically tested. |
| position_and_motion | software_fixture | Sparse/live position, directional learned bands, 90% animation, stop/reversal and queued commands | Normal-use hardware confirmation remains part of the beta campaign; no unattended movement test is run. |
| parameters_and_permissions | software_fixture | Scalar/indexed metadata, model restrictions, menu rights, scope and exactly-once readback | Per-motor writes and native menu/index semantics remain blocked rather than inferred. |
| diagnostics_privacy | software_fixture | Event/history interpretation, retained flags, receiver suppression and bounded export | Unknown native receiver/history structures are not published as decoded data. |
| installer_interruption | software_fixture | Durable intent, worker/storage drain, irreversible cancellation boundaries and recovery acknowledgement | No live learning, reset, restore, enrollment or firmware mutation is performed. |
| ha_native_boundary | software_fixture | No duplicate organization/scheduler, fixed read-only cloud paths, correct category guard and secret cleanup | Cloud copies are not proof of device execution, timezone or an actual HA conflict. |
| live_cloud_empty_account | live_scope_verified_content_pending | b10 beta feedback verified account/accessory binding and reported no configured records, but collection shapes were unrecognized | Raw replies were deliberately not retained. b11 supports explicit successful null collections and safe shape evidence; live content needs rechecking on b11. |
| core_readonly | source_and_software_fixture | Recovered shared NHK session/TABLE_READ and version-bracketed rule/scene counts | No physical CORE target was supplied. Actuation, schedule interpretation and actual execution remain unverified. |
| proview_nfc_native | source_only_blocked | ProView/NFC/receiver and model-specific native dependencies | No matching Pro native library/host adapter or new hardware fixture authorizes activation. |

The matrix is machine-readable in [validation_matrix.json](../custom_components/nice_bidiwifi/validation_matrix.json); CI checks its regression references. Fixture/software success does not establish universal firmware compatibility. Installer mutations, full CORE/ProView control, missing native decoders and model-specific writes remain blocked with exact missing/completion records in the gap register. No live gate movement, relearning, reset, receiver enrollment, firmware write, cloud-resource change or HA-automation mutation was performed for closure.

## Extending a verified binding or adapter

See [Contributor completion examples](completion_examples.md). Source reconciliation and a filled number alone are not authorization. A simple action/parameter addition belongs in its catalog/profile/codec and relevant factory derives its entities; it must retain advertised support, permission and dispatch checks. A complex procedure must provide every step, target/ownership prerequisite, bounded completion, cancellation boundary and recovery contract. Unknown slots, failed freshness or missing recovery must remain negative tests before activation.

## Reproduce the closure

Run `python scripts/reconcile_parity.py --check` and `python scripts/render_capabilities.py --check` with the test dependencies installed. To re-verify the private supplied exports, pass `--welcome /path/to/welcome-1.3.1 --pro /path/to/pro-2.18` to the reconciliation script. Only relative provenance, hashes and bounded symbolic evidence are retained, not app code or research/account exports. Changing a source hash requires a deliberate new evidence review; it cannot silently activate a feature.

B11 is the phase testing beta. It does not authorize a stable release. Collect ordinary-use feedback and the outstanding matrix evidence before a separate release-candidate/stable readiness decision.
