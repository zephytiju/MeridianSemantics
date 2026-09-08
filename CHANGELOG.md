<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

## 2.0.1 — 2026-09-08

- Express the consumed Core API as >=1.0.1,<2 instead of an exact historical recipe.
- Validate Core 1.0.1 and 1.1.0 across Python 3.12–3.14; retain exact public
  dependency locks and hashes for release verification.
- Preserve structured put/Catalog 2.0.0, cache/other Operations 1.0.0, immutable
  Schema fingerprints, and the rejection of expected-version-zero creation.
- Keep the tested release ledger separate from the dependency compatibility range.

## 2.0.0 — 2026-09-06

- Change structured put's default from implicit upsert to explicit `if_absent`,
  with `update` and `upsert` modes and existing optional CAS semantics.
- Require mode in serialized put input; publish structured Catalog and put
  Operation 2.0.0 contracts. Never reinterpret legacy omitted modes.
- Reject non-null expected versions for if_absent, including zero; preserve mode
  in fingerprints and canonicalize omitted/null optional expected versions.
- Publish portable wire/fingerprint and existence/version fixtures, an arguments
  JSON Schema, and explicit caller migration examples.
- Pin released Core 1.0.1; retain cache/other Operation versions, Schema formats,
  data-field/update-version behavior, result/error contracts and replay machinery.

## 1.0.0 — 2026-08-25

- Implement canonical `meridian.schema.v1` Schema, Field, type, index, policy,
  extension, compatibility, and fingerprint contracts.
- Add dynamic immutable Schema publication, read, update, version, deprecation,
  revision, discovery, and Core schema-provider integration.
- Add Record, RecordRef, Relation, Collection, Object, and cache metadata values
  plus portable validation for every V1 logical type and profile.
- Add multilingual BCP 47/ICU metadata and deterministic compatibility reports.
- Add structured/cache mapping-first Catalog surfaces and serialized Core
  Operation normalization without Adapter/Engine concepts.
- Add unit, integration, property, conformance, packaging, reproducibility, SBOM,
  provenance, and release automation.
