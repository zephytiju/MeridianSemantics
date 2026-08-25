<!-- SPDX-License-Identifier: Apache-2.0 -->

# Schema compatibility

`classify_compatibility(source, target)` emits ordered changes and one highest
severity classification:

1. `identical`
2. `metadata-compatible`
3. `backward-compatible`
4. `data-compatible-physical-change`
5. `conditionally-compatible`
6. `breaking`

Examples include nullable or deterministically defaulted field additions as
backward compatible, index additions as data-compatible physical changes,
integer/decimal widening as conditional on Adapter proof, and field removal,
identity change, semantic-kind change, Relation endpoint change, narrowing, or a
new required field without default as breaking.

Classification is advisory input to activation, not migration execution. The
metadata repository rejects breaking publication unless the caller explicitly
sets `allow_breaking=True`; downstream lifecycle tooling must still supply and
review the transform, validate preconditions, and execute recovery-safe migration.
