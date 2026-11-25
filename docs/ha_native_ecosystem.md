# HA-native integration and Nice automation visibility

Phase 7 exposes Nice device capabilities and relevant existing vendor state while relying on HA's own organization and automation systems. It does not rebuild MyNice inside HA.

## What stays in HA or the manufacturer app

| Capability | Disposition | Execution/ownership | Behavior |
| --- | --- | --- | --- |
| Read-only Nice automation inspection | implemented | Nice cloud record; execution unverified | Explicit one-time account/device-scoped reads. No resource is imported, changed or executed. |
| Homes, rooms and areas | ha_native | Home Assistant | Use HA areas and floors to organize entities; Nice home/room resources are not synchronized. |
| Dashboard and favorites organization | ha_native | Home Assistant | Use HA dashboards; the integration creates no dashboard or favorites mirror. |
| Groups of HA entities | ha_native | Home Assistant | Use native HA groups/helpers. Existing Nice module-group observations do not create HA groups. |
| Scenes and macros | ha_native | Home Assistant | Use native HA scenes/scripts; existing Nice scenes remain external records and are never replayed or mirrored. |
| Schedules and rules | ha_native | Home Assistant | Use HA schedule helpers and automations. There is no second scheduler in the integration. |
| Notifications | ha_native | Home Assistant | Use native HA automations/notification actions with existing Nice entities and protocol events. The integration sends no notification. |
| HA users and permissions | ha_native | Home Assistant | HA users, Nice home membership and module permissions are different authorization scopes. |
| Nice account ownership and sharing | manufacturer_app | Nice account | Manage Nice ownership, invitations and cloud sharing in the manufacturer app; module admin is not cloud ownership. |
| Manage existing vendor automations | manufacturer_app | Nice/CORE | Use the manufacturer app to edit or disable existing Nice scenes/rules/timers. HA never edits them or silently creates equivalents. |
| Vendor assistant/service linking | manufacturer_app | Nice/vendor service | Nice account linking and vendor-managed assistant services remain in the manufacturer app. HA integrations are configured natively in HA. |
| Verify actual CORE automation execution | blocked | Nice CORE | Cloud records do not establish current module table contents, execution state or schedule timezone. |
| Additional Nice device categories | blocked | Nice device | Domain contracts are declared; missing control/state/transport codecs do not create fake light, relay, lock or blind entities. |

No HA/Nice homes, rooms/areas, dashboards, groups, scenes, rules, schedules, notifications or users are created or synchronized by this phase. The integration does not run a second scheduler, execute a cloud scene/rule or silently import an HA equivalent. Existing module permissions stay separate from HA users and Nice account membership. Manage original Nice resources and vendor account/assistant linking in the manufacturer app.

## Review existing controller behavior without cloud access

Configure > HA and existing Nice automations > Review controller automatic closing reads only values already observed by the parameter engine for automatic close, close after photocell and always close. It shows source/time, keeps unobserved or unsupported values unknown, and does not perform extra reads or change settings. These controller functions can operate independently of HA; review any equivalent HA logic before changing anything. Their presence is not proof of an automation conflict.

## Optional read-only Nice cloud inspection

Use the same Configure submenu to request a temporary MyNice login. It uses the app's built-in registration and private HTTPS service. After explicit consent it requests an OAuth token, GET api/v1/macrouser/user to bind the account to this configured accessory, then reads the bound home's scenes and rule/timer groups from that successful macro-user inventory, as the app's main synchronization does. It filters records to the configured accessory and retains only timers linked to the same scoped rules. The separate per-accessory /scene and /rules endpoints are not queried. It has no resource POST/PUT/DELETE, synchronization or execution operation. The only POST is authentication, followed by one macro-user GET. There is no automatic polling or refresh-token session.

The binding uses an exact accessory match in the returned home list or a returned per-accessory credential record, without retaining/replacing any NHK credentials. Internal identity comparison is normalized, while cloud queries preserve the exact verified accessory identifier returned by the service, matching the app rather than rewriting REST identifiers into NHK MAC notation. Cloud home role is reported separately from module access. A different account/device scope, duplicate identity or orphan timer is rejected. Timers must link to rule IDs from the same response and accessory. The scope is the accessory/gateway; a record may target another attached device. Logical-device mapping is explicitly unverified.

The local inspection screen lists names, reported status codes and raw Nice time/repeat/dayTime fields so you can recognize records in MyNice. These fields are not converted into HA schedules. Current CORE table contents, activation, actual execution, timezone and conflicts with HA automations remain unverified. A cloud record is a cloud copy, not proof that a gate action is currently scheduled. Missing/malformed/unavailable collections stay unknown; only explicit valid empty lists mean no records reported in that cloud response.

Names and exact schedules stay only in bounded runtime memory and the authenticated options screen until replaced/unloaded. Public diagnostics and the optional disabled-by-default Nice automation inspection sensor contain collection states/counts, capture/attempt times, reported role and explicit uncertainty, never private names, account/home/resource IDs, schedules, member details, home addresses, raw payloads or credentials. A failed new attempt preserves the old capture's original time and records the error type; it cannot make stale data look fresh. Closing/reloading the entry cancels the owned request and discards its private snapshot.

Account/password/token references are cleared on success, rejection, timeout and cancellation. OAuth expiry uses elapsed monotonic time and does not trigger an automatic retry. Both this inspection and one-time credential import use isolated cookie-free HTTP sessions, detached afterward without closing HA's shared verified TLS connector. Redirects are disabled. This cannot change local/cloud/fallback mode, per-device NHK credentials, permissions, calibration or learned movement profiles.

## Appropriate HA domains

Existing gate/door controls retain their unique IDs and existing command/state adapter. Explicit gate types use the gate cover class; sectional/up-and-over types use garage and other known door types use door. Unverified screen subtypes keep an unspecified class rather than guessing blind or awning. Unknown legacy types keep existing compatibility rather than claiming a verified new category. Known lights, relays, electrical locks and screens do not inherit gate controls: they need their own verified state/command/transport adapters. Cloud metadata alone never creates actuator entities or rewrites a loaded device profile. A courtesy-light toggle button does not establish a stateful light entity.

| Reported app/INFO type | HA domain contract | Device class | Adapter disposition |
| --- | --- | --- | --- |
| `BARRIER` | cover | gate | existing cover adapter with observed capabilities |
| `BLIND` | cover | blind | ecosystem.device_adapter |
| `DIMMER` | light | none | ecosystem.device_adapter |
| `DIMMER.LIGHTS` | light | none | ecosystem.device_adapter |
| `DOOR.DEFAULT` | cover | door | existing cover adapter with observed capabilities |
| `DOOR.ROLL_UP` | cover | door | existing cover adapter with observed capabilities |
| `DOOR.SECTIONAL` | cover | garage | existing cover adapter with observed capabilities |
| `DOOR.SIDE_HINGED` | cover | door | existing cover adapter with observed capabilities |
| `DOOR.SIDE_TO_SIDE` | cover | door | existing cover adapter with observed capabilities |
| `DOOR.TILT_UP` | cover | garage | existing cover adapter with observed capabilities |
| `ELECTRICAL_LOCK` | lock | none | ecosystem.device_adapter |
| `GARAGE` | cover | garage | existing cover adapter with observed capabilities |
| `GATE` | cover | gate | existing cover adapter with observed capabilities |
| `GATE.DEFAULT` | cover | gate | existing cover adapter with observed capabilities |
| `GATE.PARKING` | cover | gate | existing cover adapter with observed capabilities |
| `GATE.PEDESTRIAN` | cover | gate | existing cover adapter with observed capabilities |
| `GATE.SLIDING` | cover | gate | existing cover adapter with observed capabilities |
| `GATE.SWING` | cover | gate | existing cover adapter with observed capabilities |
| `LIGHT` | light | none | ecosystem.device_adapter |
| `LOCK` | lock | none | ecosystem.device_adapter |
| `RELAY` | switch | none | ecosystem.device_adapter |
| `RELAY.DEFAULT` | switch | none | ecosystem.device_adapter |
| `RELAY.ELECTRICAL_LOCK` | lock | none | ecosystem.device_adapter |
| `RELAY.LIGHTS` | light | none | ecosystem.device_adapter |
| `RELAY.POWERLOAD` | switch | none | ecosystem.device_adapter |
| `SCREEN` | cover | none | ecosystem.device_adapter |
| `SCREEN.BLIND` | cover | blind | ecosystem.device_adapter |
| `SCREEN.DEFAULT` | cover | none | ecosystem.device_adapter |
| `SCREEN.MONO` | cover | none | ecosystem.device_adapter |
| `SCREEN.PATIO` | cover | none | ecosystem.device_adapter |
| `SCREEN.SHUTTER` | cover | shutter | ecosystem.device_adapter |
| `SCREEN.VENETIAN` | cover | blind | ecosystem.device_adapter |
| `SECTIONAL` | cover | garage | existing cover adapter with observed capabilities |
| `SLIDING` | cover | gate | existing cover adapter with observed capabilities |
| `SWING` | cover | gate | existing cover adapter with observed capabilities |
| `UP AND OVER` | cover | garage | existing cover adapter with observed capabilities |
| `UP_AND_OVER` | cover | garage | existing cover adapter with observed capabilities |

## Use native HA for new logic

Assign existing Nice entities to HA areas and dashboards normally. Build new schedules using HA automations/schedule helpers and the existing cover actions. Build notifications with HA automation conditions and native notification actions; the integration never sends them itself. The existing Protocol event entity exposes normalized events for native HA triggers, with deduplication and reported/inferred cause distinctions preserved.

Before adding a timed close, review controller automatic-close modes and any Nice rules/timers in the manufacturer app. Decide yourself whether to keep or disable the original behavior. Do not run both copies of an action merely because a similarly named cloud record exists. The integration does not automatically edit, disable, recreate or compare your HA/Nice automations.

## Missing evidence and source basis

B11 provides an explicit count-only CORE table reader using the shared NHK session and source-backed TABLE_READ. It brackets snapshots with versions and retains no rows. Actual execution and schedule semantics remain unverified; this does not activate generic CORE gate/control adapters. See the parity closure guide for the standalone tool. Additional device adapters need category-specific control/state fixtures and transport. These are genuine gaps; HA-owned software and manufacturer-app account operations are deliberate boundaries. The source inventory routes reviewed home/scene/rule/account UI/API families to the corresponding dispositions; other entries retain their initial routing for phase 8 closure.

Source basis: Welcome 1.3.1 NhkApiService, UserMacroDataResponse/Data/Home/Accessory/User, GetScenariosResponse, GetAllRulesResponse/RulesWithTimers, Rule/RuleTimer/Scenario and AutomationType, plus Scene/Rule/ScheduleRepositoryImpl device/cloud synchronization paths. No live account resources or gate settings were changed during implementation. New vendor inspection needs real read-only beta validation with an account associated with the configured accessory.

## Beta checks

Update/restart HA without re-pairing or recalibrating. Check existing controls, IDs, position, registry choices and local recovery. Review already-observed controller modes with no cloud account. Optional cloud inspection should require consent, display source/age/uncertainty, clear secrets and leave all existing HA/Nice resources and connection policy unchanged. Compare record names/times privately with MyNice; diagnostics should contain only the safe summary. Blocked CORE readback must not open a connection. The new sensor starts disabled and does not initiate inspection.
