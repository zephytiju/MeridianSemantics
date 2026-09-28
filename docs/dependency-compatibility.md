<!-- SPDX-License-Identifier: Apache-2.0 -->

# Core dependency compatibility

The manifest declares the internal dependency major-only,
`meridian-storage-core>=1,<2` (the jumbo accepted range form; resolution takes
the newest promoted build of major 1 from the Jumbo index). The proven public
API bound remains >=1.0.1,<2 — the lower bound retains the Core release used by
the structured put v2 public API and is ledgered below. Semantics
consumes Core Expression/Operation and their serializers, ResourceRef/SchemaRef,
Catalog provider manifests and discovery, registry definitions/bundles, and the
public error hierarchy. These public APIs are exercised by the existing
contract, property, publication, and Catalog integration suites with both Core
1.0.1 and 1.1.0. The next major Core release is excluded pending API review.
The range permits dependency resolution; it does not certify every future release.

The structured Catalog and `meridian.structured.put` Operation remain 2.0.0.
Cache and other Operations remain 1.0.0. Schema/resource wire formats and golden
fingerprints are unchanged. No persisted expression, record, or Schema migration
is needed from Semantics 2.0.0. The previous migration from implicit upsert is
still mandatory; see [structured put migration](structured-put-migration.md).
In particular, a non-null expected version is invalid for `if_absent`, including
zero, and update/upsert CAS still requires an existing matching version.

## Release metadata and contract gates

| Surface | Classification | Behavior |
| --- | --- | --- |
| Package Requires-Dist `core` and public API ledger `core` | Public API bound | Requires-Dist declares the major only (>=1,<2); proven API bound >=1.0.1,<2; no exact historical recipe |
| `compatibility.json` `core` and `testedCoreReleases` | Tested-release provenance | Exact selected Core version, commit and public artifact hashes; never runtime membership gates |
| `locks/core-*.txt` | Release-validation integrity | Exact public dependency closure with SHA-256 verification |
| Catalog, Operation, Schema and capability requirements | Semantic contracts | Unchanged; incompatible Operations still fail closed |
| Schema and Operation canonical fingerprints | Content integrity | Existing independent golden fixtures remain exact |

Core has no runtime dependencies, so each lock completely describes the external
runtime dependency closure. CI runs every existing quality and coverage gate on
Python 3.12, 3.13 and 3.14 with both locked releases. Packaging and publication use
Core 1.1.0. Installation uses ordinary pip dependency resolution, with `pip check`;
there are no overrides, dependency suppression or sibling source imports.

```sh
python -m venv .validation
.validation/bin/python -m pip install -r locks/core-1.1.0.txt
.validation/bin/python -m pip install '.[test]'
.validation/bin/python -m pip check
.validation/bin/python scripts/verify_contracts.py
.validation/bin/python -m pytest --cov=meridian_storage.semantics
```

This repository's integration gates exercise the engine-neutral Core Catalog and
metadata-publication APIs. It has no engine runtime or engine test service.
Adapter execution, real-engine existence/CAS behavior and cross-package platform
conformance remain the owning adapter and downstream conformance tasks' gates.
No new engine combination or blanket platform compatibility is claimed here.
