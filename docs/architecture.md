<!-- SPDX-License-Identifier: Apache-2.0 -->

# Architecture and authority boundaries

This package owns the provider-neutral logical model, validation, compatibility
classification, dynamic metadata API, structured/cache Catalog manifests and
Expression normalization, and logical Object/cache metadata. It consumes only
the public `meridian-storage-core==1.0.0` release.

It deliberately does not own:

- Object Catalog operation surfaces, which belong to object-common.
- Evidence Catalog operation surfaces, which belong to evidence.
- Streaming or Kafka behavior, which belongs to downstream streaming packages.
- Shared query/projection library implementation.
- Adapter/Engine selection, endpoint or credential configuration, physical
  provisioning, DDL, state, identity, ACLs, migration execution, recovery, or
  lifecycle orchestration.
- Platform or application concepts and third-party product-private databases.

The `SemanticsAdapter` protocol is the narrow activation boundary. It accepts
validated logical Schema and Collection values, returns deterministic activation
plans/results, encodes and decodes logical data, and imports/exports portable
logical representations. Plans contain logical references, steps, requirements,
and metadata only—never Engine identities or secrets.

`InMemoryMetadataRepository` is a deterministic reference implementation for
tests, conformance, and local tooling. Production persistence is Adapter-owned.
Its lock and compare-and-set revision demonstrate required atomicity; they do not
claim to be a production metadata service.

The authoritative baseline implemented by 1.0.0 is Meridian HLD revision 56 and
Meridian Catalogs and Public Interfaces revision 70. No architecture/interface
change is introduced by this repository.
