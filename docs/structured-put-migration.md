<!-- SPDX-License-Identifier: Apache-2.0 -->

# Migrate structured.put to explicit existence modes

Semantics 2.0.0 requires released Core 1.0.1. Its structured Catalog contract and
`meridian.structured.put` Operation are 2.0.0; other Operations, the cache Catalog,
Schema formats and the schema-provider contract remain 1.0.0. Install an adapter
that advertises `meridian.structured.put` 2.0.0 before enabling these writes.
Core rejects incompatible capability versions before execution. Never substitute
an old adapter's upsert for an unsupported mode.

```python
structured = meridian.catalog("structured")

# Initialize once. A distinct attempt against an existing identity conflicts.
created = meridian.execute(structured.put(
    resource="example.customers", data={"id": "1", "name": "Ada"},
    mode="if_absent",  # also the default for new Python calls
))

# Deliberately update an existing identity, optionally checking a retained version.
meridian.execute(structured.put(
    resource="example.customers", data={"id": "1", "name": "Grace"},
    mode="update", expected_version=retained_version,
))

# Preserve an intentional former implicit-upsert call explicitly.
meridian.execute(structured.put(
    resource="example.customers", data={"id": "1", "name": "Ada"},
    mode="upsert",
))
```

Retain the version returned by Meridian using the existing result contract.
`expected_version` is optional for update/upsert. When provided it requires an
existing record at that version; absence or mismatch conflicts. Zero never means
create. Any non-null expected version, including zero, is invalid with if_absent.
Each mode applies within one logical Resource and mandatory scope.

| Mode | Absent, no expected version | Present, no expected version |
| --- | --- | --- |
| if_absent | Create | ConflictError |
| update | ConflictError | Update |
| upsert | Create | Update |

This release changes existence behavior only. Data-field update behavior,
update-version behavior, result shape, error types/codes, and recognized-request
replay are unchanged. Equal data does not establish replay. No full-record
replacement rule or replay framework is introduced. Object and cache put are
unchanged.

## Serialized callers

The Core Expression envelope stays `meridian.expression.v1`. Its structured put
arguments **must** contain `mode`; the public method inserts it before serialization.
The Core Operation envelope stays `meridian.operation.v1` and contains
`operationVersion: "2.0.0"`, an explicit mode in `input`, and a matching 2.0.0
capability requirement. Generic envelope decoding does not grant execution support.
Old Expressions without mode fail Semantics normalization. Old Operations retain
their original version; never relabel or silently migrate them.

Inspect each persisted caller's intent and reconstruct a new Expression with an
explicit mode. Use `upsert` only when the prior caller intentionally meant upsert;
choose `if_absent` for initialization and `update` for existing-state changes.
All modes affect the normalized request fingerprint. Omitted and null optional
expected versions normalize identically. Never reuse old fingerprints as new ones.

## Portable conformance

`contracts/operations/meridian.structured.put.v2.schema.json` validates arguments
and normalized input. `contracts/conformance/structured-put.v2.json` publishes
complete wire documents and canonical fingerprints, invalid/legacy input, and the
absent/present and version matrix. Adapters consume `existenceCases` against their
real engines; Semantics verifies syntax and normalization, not storage execution.
The fixture's current version 1 is sample state, not a universal initial version.
