# Completing a binding, codec or procedure

The source inventory is evidence, never a dispatch table. A new export, enum name
or numeric value cannot enable an operation by itself. Keep HA organization and
execution native; do not add a Nice scheduler or cloud-resource mirror.

## A simple symbolic binding

1. Record the app version, relative source path/hash or sanitized request/reply
   fixture establishing the actual wire code. An app enum ordinal is insufficient.
2. Add the action to `protocol/t4/actions.py` or a parameter definition/profile to
   the parameter catalog. Existing factories derive their entities from those
   definitions. Preserve IDs of existing definitions; no coordinator/platform
   rewrite is needed to add a verified catalog entry.
3. Retain the device-advertised support mask, writable service permission, current
   module role, model-specific restrictions and before-dispatch checks. Zero is a
   valid resolved value; `None` is unresolved. A global numeric binding is not
   evidence that every controller supports it.
4. Add positive checked-reply fixtures plus refusal, wrong target, unknown
   metadata, stale scope and uncertain-delivery tests. A lost response must not
   replay a write. Only then change the feature status and regenerate documents.

`tests/test_completion_contracts.py` demonstrates that a required unresolved slot
cannot pass preflight, while a zero-valued slot can resolve. This checks the
extension contract, not hardware support; model/role checks still apply before
actual dispatch. The current factory/catalog integration tests establish stable
entity IDs and derived entities.

## A new structured reader

Use an allowlisted request and a bounded, typed decoder. Validate request type,
target scope, complete metadata/record identity and continuation. Keep unknown
fields unavailable; never convert missing data into zero or a presumed state.
Retain only reviewed public fields. Receiver IDs, rolling codes, certificates,
passwords and raw opaque payloads must not enter diagnostics or incident history.

The CORE count-only reader is a worked example: recovered `TABLE_READ` bodies,
explicit CORE identity, fixed table IDs, version bracketing and no rows in its
public report. Adding actual runtime/schedule interpretation requires independent
source/capture evidence; a row count cannot establish execution or timezone.
`tests/test_core_tables.py` exercises both the reader and its rejection boundaries.

## A stateful installer adapter

Provide a full `InstallerProcedure`: named steps/handlers, exact device and role
prerequisites, per-step deadlines, completion evidence and recovery. Every
mandatory binding must resolve before preparation, persistence or network work.
Use the existing `ProcedureRunner`, which owns one operation, persists intent
before mutation and drains network/storage work before releasing ownership.

The fixture procedure in `tests/test_completion_contracts.py` plugs a read adapter
into the existing runner without editing HA entities or the coordinator. A real
mutation additionally needs explicit intent, current scope and physical-state
checks, irreversible cancellation boundaries and a tested interrupted-write path.
A user cancellation request is not proof that the hardware stopped. Do not add a
resume/retry path until the device protocol makes it verifiable.

## Reconcile and validate

Update the semantic feature, exact gap/activation criteria, source disposition and
validation level together. Run:

```sh
python scripts/reconcile_parity.py --check
python scripts/render_capabilities.py --check
python -m pytest tests
python -m ruff check .
```

When new private exports arrive, re-verify their indexed hashes with the source
root arguments documented in `parity_closure.md`. Do not commit app code, account
captures or private research exports. Mark source, fixture and live hardware
validation separately. Passing the suite or closing an evidence row does not
justify a stable release or activating an unverified device family.
