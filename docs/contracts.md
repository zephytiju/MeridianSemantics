<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian V1 semantics contracts

## Canonical Schema

`SchemaDocument` serializes as `meridian.schema.v1`. Canonical JSON is UTF-8,
uses lexically sorted object keys and no insignificant whitespace, and rejects
non-finite numbers. The SHA-256 fingerprint is computed over the full canonical
document except the derived `fingerprint` member.

An exact Schema address is `Catalog:Namespace.name@SemVer`; a latest-version
reference omits `@SemVer`. Routing names use the exact ASCII logical-name grammar
released by Core 1.0.0. Consumer-visible field names, labels, annotations, and
localized metadata are Unicode NFC. This distinction preserves byte-compatible
Core routing while allowing multilingual logical metadata.

The V1 logical types are boolean, signed 8/16/32/64-bit integers, bounded
decimal, finite float64, string, base64url bytes, UUID, UTC timestamp, date,
duration, enum, JSON, RecordRef, ObjectRef, and WGS84 point. Cardinality and
nullability are independent. Only bounded deterministic default expressions are
accepted: literal, field, concat, coalesce, lower, and upper.

## Dynamic Schema API

`SchemaAPI.publish`/`create`, `read`/`get`, `versions`/`list_versions`, `update`,
and `deprecate` operate through a metadata repository revision. Exact retries are
idempotent. A conflicting fingerprint, non-monotonic version, stale revision, or
unapproved breaking change produces a stable typed error. `update` always creates
a new immutable version; it never edits a published document.

## Resource and data documents

- `CollectionDocument` binds one structured logical Resource to an exact active
  Schema and semantic profile, with lifecycle state and policy overrides.
- `Record` carries the Collection, scalar or ordered compound identity, values,
  version, and canonical UTC timestamps.
- `RelationProfile` declares directedness, endpoint fields, endpoint Collection
  allowlists, and required endpoint indexes. `all_neighbors` is expanded against
  a fingerprinted registry snapshot before an Operation is serialized.
- `ObjectMetadata` binds an ObjectRef, SHA-256 digest, byte length, media type,
  creation context, user metadata, mutability, and provenance.
- `CacheEntry` includes serializer and Schema fingerprints, timestamps, expiry,
  and source version. Cache state is never authoritative.

## Multilingual metadata

`LocalizedText` stores normalized BCP 47 locale keys and validated ICU
MessageFormat patterns. Resolution tries the exact locale, successively less
specific parents, then the declared default locale. V1 supports simple arguments,
select, plural, and selectordinal, including exact numeric selectors and bounded
non-negative plural offsets. English, Chinese/Japanese/Korean, French/Portuguese,
and Russian/Ukrainian plural behavior is deterministic without provider code.

## Stable failures

All public failures extend Core typed error categories and preserve a stable code,
optional normative `requirement`, and sorted `logicalReferences`. The complete
code set is locked in `contracts/public-api/meridian-semantics.v1.json`.
