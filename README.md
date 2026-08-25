<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian Storage Semantics

`meridian-storage-semantics` is the independently released Meridian V1 logical
schema, resource, data, and query-facing metadata package. It provides portable
contracts for Records, Relations, Objects, cache entries, dynamic Schema
publication, compatibility classification, multilingual ICU metadata, and Core
mapping-first Catalog surfaces.

The distribution contains one Python package, `meridian_storage.semantics`, and
depends on the released `meridian-storage-core==1.0.0` contract. The shared
`meridian_storage` import root is packaging infrastructure; this repository does
not contain Core source or any other Meridian package.

## Install

```console
python -m pip install meridian-storage-semantics==1.0.0
```

Python 3.12 or newer is required.

## Define and publish a Schema

```python
from meridian_storage.semantics import InMemoryMetadataRepository, SchemaAPI

schemas = SchemaAPI(InMemoryMetadataRepository())
published = schemas.publish(
    namespace="investigation",
    name="case",
    version="1.0.0",
    definition={
        "semanticKind": "relational",
        "fields": [
            {"name": "case_id", "logicalType": "uuid"},
            {"name": "title", "logicalType": "string"},
        ],
        "identity": ["case_id"],
    },
)

assert published.publication.document.format_version == "meridian.schema.v1"
```

Schema versions are immutable. Publishing an identical version and fingerprint
is idempotent; a different document under the same version is rejected. New
versions are ordered by SemVer and classified before activation.

## Build mapping-first Expressions

```python
from meridian_storage.semantics import StructuredCatalogProvider

provider = StructuredCatalogProvider()
expression = provider.create_surface().query(
    resource="investigation.cases",
    where={"status": {"eq": "open"}},
    select=("case_id", "title"),
    limit=50,
)
operation = provider.normalize(expression)
```

Consumers create serializable Core `Expression` values. Planning normalizes them
to portable `Operation` values. Engine selection, credentials, provisioning,
physical DDL, ACLs, migration execution, and lifecycle remain Adapter/Platform
responsibilities and are not accepted by these APIs.

## Catalog registry

The V1 registry is exactly:

- `structured` — owned here; relational, document, key-value, search,
  geospatial, time-series, and relation profiles.
- `object` — owned by `meridian-storage-object-common`; object, artifact, and
  media profiles. This package supplies their logical metadata values.
- `cache` — owned here; explicit, non-authoritative cache semantics.
- `evidence` — owned by `meridian-storage-evidence`; telemetry, audit, lineage,
  and provenance profiles.
- `streaming` — accepted-pending and owned by the downstream streaming package.

Query, projection, ontology, search, geospatial, time-series, relation,
artifact, media, telemetry, audit, lineage, and provenance are not Catalogs.
`NativeQuery` is post-V1. No Kafka or streaming-provider behavior is embedded.

## Contracts and verification

Language-neutral JSON contracts live in [`contracts`](contracts), including the
canonical `meridian.schema.v1` schema, wire documents, public API ledger, exact
Catalog registry, and valid/invalid conformance fixtures. Compatibility with
Core and the design revisions is pinned in [`compatibility.json`](compatibility.json).

Run all local gates after installing test dependencies:

```console
python -m pip install '.[test]'
ruff format --check src tests scripts
ruff check src tests scripts
mypy src
python scripts/verify_contracts.py
pytest --cov=meridian_storage.semantics --cov-report=term-missing
```

Release builds are created twice with a fixed `SOURCE_DATE_EPOCH`, compared
byte-for-byte, inspected for repository/package boundaries and license metadata,
and accompanied by SPDX SBOM and GitHub build-provenance attestations.

Further detail is in [`docs/contracts.md`](docs/contracts.md) and
[`docs/architecture.md`](docs/architecture.md).

## License

Copyright 2026 Meridian contributors. Licensed under Apache License 2.0; see
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
