# SPDX-License-Identifier: Apache-2.0
"""Thread-safe dynamic Schema/Resource lifecycle and Core schema-provider integration."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from meridian_storage.registry import (
    NamespaceDefinition as CoreNamespaceDefinition,
)
from meridian_storage.registry import ResourceBundle as CoreResourceBundle
from meridian_storage.registry import ResourceDefinition as CoreResourceDefinition

from ._version import __version__
from .canonical import JsonValue, SemanticVersion, sha256_fingerprint
from .compatibility import CompatibilityReport, classify_compatibility
from .errors import (
    ActivationFailed,
    CapabilityMismatch,
    IncompatibleSchema,
    RegistryRevisionConflict,
    ResourceAlreadyExists,
    ResourceNotFound,
    SchemaVersionConflict,
)
from .profiles import RelationProfile
from .resources import (
    CatalogName,
    CollectionDocument,
    CollectionState,
    ResourceReference,
    SchemaReference,
)
from .schemas import SchemaDocument
from .spi import ActivationResult, SemanticsAdapter
from .validation import validate_schema

SEMANTICS_CONTRACT_VERSION = "1.0.0"
STRUCTURED_REGISTRY_REF = ResourceReference(CatalogName.STRUCTURED, "meridian", "registry")
CACHE_REGISTRY_REF = ResourceReference(CatalogName.CACHE, "meridian", "registry")


class SchemaStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


@dataclass(frozen=True, slots=True)
class PublishedSchema:
    document: SchemaDocument
    status: SchemaStatus
    published_at: str
    deprecated_at: str | None = None

    @property
    def ref(self) -> SchemaReference:
        return self.document.ref

    @property
    def fingerprint(self) -> str:
        return self.document.fingerprint

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.document.to_dict(),
            "status": self.status.value,
            "publishedAt": self.published_at,
            "deprecatedAt": self.deprecated_at,
        }


@dataclass(frozen=True, slots=True)
class PublishResult:
    publication: PublishedSchema
    idempotent: bool
    registry_revision: int
    compatibility: CompatibilityReport | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "publication": self.publication.to_dict(),
            "idempotent": self.idempotent,
            "registryRevision": self.registry_revision,
            "compatibility": (None if self.compatibility is None else self.compatibility.to_dict()),
        }


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    revision: int
    fingerprint: str
    schemas: tuple[PublishedSchema, ...]
    resources: tuple[CollectionDocument, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "fingerprint": self.fingerprint,
            "schemas": [item.to_dict() for item in self.schemas],
            "resources": [item.to_dict() for item in self.resources],
        }


@dataclass(frozen=True, slots=True)
class TraversalResolution:
    start: ResourceReference
    relation_collections: tuple[ResourceReference, ...]
    registry_fingerprint: str
    max_depth: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "start": self.start.to_dict(),
            "relationCollections": [item.to_dict() for item in self.relation_collections],
            "registryFingerprint": self.registry_fingerprint,
            "maxDepth": self.max_depth,
        }


class InMemoryMetadataRepository:
    """Deterministic reference repository for tests, local tools, and conformance.

    Production persistence is Adapter-owned. This implementation deliberately
    contains no Engine selection, endpoint, credential, provisioning, or DDL.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._schemas: dict[tuple[CatalogName, str, str], dict[str, PublishedSchema]] = {}
        self._resources: dict[ResourceReference, CollectionDocument] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def publish_schema(
        self,
        document: SchemaDocument,
        *,
        expected_revision: int | None = None,
        allow_breaking: bool = False,
    ) -> PublishResult:
        validate_schema(document)
        key = (document.ref.catalog, document.ref.namespace, document.ref.name)
        version = cast(str, document.ref.version)
        with self._lock:
            self._require_revision(expected_revision)
            versions = self._schemas.setdefault(key, {})
            existing = versions.get(version)
            if existing is not None:
                if existing.fingerprint == document.fingerprint:
                    return PublishResult(existing, True, self._revision)
                raise SchemaVersionConflict(
                    f"Schema {document.ref.canonical} already has a different fingerprint",
                    requirement="schema.version-immutable",
                    logical_references=(document.ref.canonical,),
                    resource_ref=document.ref.schema_id,
                )
            previous = _latest(tuple(versions.values()), include_deprecated=True)
            report: CompatibilityReport | None = None
            if previous is not None:
                if SemanticVersion.parse(version) <= SemanticVersion.parse(
                    cast(str, previous.ref.version)
                ):
                    raise SchemaVersionConflict(
                        "new Schema version must be greater than every published version",
                        requirement="schema.version-monotonic",
                        logical_references=(previous.ref.canonical, document.ref.canonical),
                        resource_ref=document.ref.schema_id,
                    )
                report = classify_compatibility(previous.document, document)
                if report.breaking and not allow_breaking:
                    raise IncompatibleSchema(
                        "breaking Schema change requires explicit allow_breaking and migration",
                        requirement="schema.breaking-explicit",
                        logical_references=(previous.ref.canonical, document.ref.canonical),
                        resource_ref=document.ref.schema_id,
                    )
            publication = PublishedSchema(
                document,
                SchemaStatus.PUBLISHED,
                _timestamp(self._clock()),
            )
            versions[version] = publication
            self._revision += 1
            return PublishResult(publication, False, self._revision, report)

    def get_schema(
        self,
        reference: SchemaReference,
        *,
        include_deprecated: bool = False,
    ) -> PublishedSchema:
        ref = SchemaReference.parse(reference)
        key = (ref.catalog, ref.namespace, ref.name)
        with self._lock:
            versions = tuple(self._schemas.get(key, {}).values())
            if ref.version is None:
                publication = _latest(versions, include_deprecated=include_deprecated)
            else:
                publication = self._schemas.get(key, {}).get(ref.version)
                if (
                    publication is not None
                    and publication.status is SchemaStatus.DEPRECATED
                    and not include_deprecated
                ):
                    publication = None
            if publication is None:
                raise ResourceNotFound(
                    f"Schema {ref.canonical} was not found",
                    requirement="schema.exists",
                    logical_references=(ref.canonical,),
                    resource_ref=ref.schema_id,
                )
            return publication

    def list_schema_versions(
        self,
        reference: SchemaReference,
        *,
        include_deprecated: bool = True,
    ) -> tuple[PublishedSchema, ...]:
        ref = SchemaReference.parse(reference)
        key = (ref.catalog, ref.namespace, ref.name)
        with self._lock:
            result = tuple(
                item
                for item in self._schemas.get(key, {}).values()
                if include_deprecated or item.status is SchemaStatus.PUBLISHED
            )
        return tuple(
            sorted(result, key=lambda item: SemanticVersion.parse(cast(str, item.ref.version)))
        )

    def deprecate_schema(
        self,
        reference: SchemaReference,
        *,
        expected_revision: int | None = None,
    ) -> PublishedSchema:
        ref = SchemaReference.parse(reference).exact()
        key = (ref.catalog, ref.namespace, ref.name)
        with self._lock:
            self._require_revision(expected_revision)
            publication = self._schemas.get(key, {}).get(cast(str, ref.version))
            if publication is None:
                raise ResourceNotFound(
                    f"Schema {ref.canonical} was not found",
                    requirement="schema.exists",
                    logical_references=(ref.canonical,),
                    resource_ref=ref.schema_id,
                )
            if publication.status is SchemaStatus.DEPRECATED:
                return publication
            deprecated = replace(
                publication,
                status=SchemaStatus.DEPRECATED,
                deprecated_at=_timestamp(self._clock()),
            )
            self._schemas[key][cast(str, ref.version)] = deprecated
            self._revision += 1
            return deprecated

    def create_collection(
        self,
        document: CollectionDocument,
        *,
        expected_revision: int | None = None,
    ) -> CollectionDocument:
        collection = replace(document, state=CollectionState.PROVISIONING)
        with self._lock:
            self._require_revision(expected_revision)
            if collection.ref in self._resources:
                raise ResourceAlreadyExists(
                    f"Resource {collection.ref.canonical} already exists",
                    requirement="resource.unique",
                    logical_references=(collection.ref.canonical,),
                    resource_ref=collection.ref.canonical,
                )
            self.get_schema(collection.active_schema_ref)
            self._resources[collection.ref] = collection
            self._revision += 1
            return collection

    def get_collection(self, reference: ResourceReference) -> CollectionDocument:
        ref = ResourceReference.parse(reference)
        with self._lock:
            result = self._resources.get(ref)
            if result is None:
                raise ResourceNotFound(
                    f"Resource {ref.canonical} was not found",
                    requirement="resource.exists",
                    logical_references=(ref.canonical,),
                    resource_ref=ref.canonical,
                )
            return result

    def replace_collection(
        self,
        document: CollectionDocument,
        *,
        expected_revision: int | None = None,
    ) -> CollectionDocument:
        with self._lock:
            self._require_revision(expected_revision)
            if document.ref not in self._resources:
                raise ResourceNotFound(
                    f"Resource {document.ref.canonical} was not found",
                    logical_references=(document.ref.canonical,),
                    resource_ref=document.ref.canonical,
                )
            self._resources[document.ref] = document
            self._revision += 1
            return document

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            schemas = tuple(
                sorted(
                    (item for versions in self._schemas.values() for item in versions.values()),
                    key=lambda item: (
                        item.ref.catalog.value,
                        item.ref.namespace,
                        item.ref.name,
                        SemanticVersion.parse(cast(str, item.ref.version)),
                    ),
                )
            )
            resources = tuple(sorted(self._resources.values(), key=lambda item: item.ref))
            revision = self._revision
        payload = {
            "revision": revision,
            "schemas": [item.to_dict() for item in schemas],
            "resources": [item.to_dict() for item in resources],
        }
        return RegistrySnapshot(revision, sha256_fingerprint(payload), schemas, resources)

    def _require_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self._revision:
            raise RegistryRevisionConflict(
                f"expected registry revision {expected}, observed {self._revision}",
                requirement="registry.compare-and-set",
            )


class SchemaAPI:
    """Mapping-first dynamic create/read/update/version API."""

    def __init__(self, repository: InMemoryMetadataRepository) -> None:
        self._repository = repository

    @property
    def registry_revision(self) -> int:
        return self._repository.revision

    def publish(
        self,
        *,
        catalog: CatalogName | str = CatalogName.STRUCTURED,
        namespace: str,
        name: str,
        version: str,
        definition: Mapping[str, object],
        expected_revision: int | None = None,
        allow_breaking: bool = False,
    ) -> PublishResult:
        try:
            document = SchemaDocument.from_definition(
                catalog=catalog,
                namespace=namespace,
                name=name,
                version=version,
                definition=definition,
            )
            validate_schema(document)
        except (TypeError, ValueError) as exc:
            from .errors import InvalidDefinition

            raise InvalidDefinition(
                str(exc),
                requirement="schema.canonical",
                logical_references=(f"{catalog}:{namespace}.{name}@{version}",),
            ) from exc
        return self._repository.publish_schema(
            document,
            expected_revision=expected_revision,
            allow_breaking=allow_breaking,
        )

    create = publish

    def read(
        self,
        *,
        catalog: CatalogName | str = CatalogName.STRUCTURED,
        namespace: str,
        name: str,
        version: str | None = None,
        include_deprecated: bool = False,
    ) -> PublishedSchema:
        return self._repository.get_schema(
            SchemaReference(CatalogName(catalog), namespace, name, version),
            include_deprecated=include_deprecated,
        )

    get = read

    def versions(
        self,
        *,
        catalog: CatalogName | str = CatalogName.STRUCTURED,
        namespace: str,
        name: str,
        include_deprecated: bool = True,
    ) -> tuple[PublishedSchema, ...]:
        return self._repository.list_schema_versions(
            SchemaReference(CatalogName(catalog), namespace, name),
            include_deprecated=include_deprecated,
        )

    list_versions = versions

    def update(
        self,
        *,
        catalog: CatalogName | str = CatalogName.STRUCTURED,
        namespace: str,
        name: str,
        current_version: str,
        version: str,
        definition: Mapping[str, object],
        expected_revision: int | None = None,
        allow_breaking: bool = False,
    ) -> PublishResult:
        current = self.read(
            catalog=catalog,
            namespace=namespace,
            name=name,
            include_deprecated=True,
        )
        if current.ref.version != current_version:
            raise RegistryRevisionConflict(
                f"expected active Schema version {current_version!r}, "
                f"observed {current.ref.version!r}",
                requirement="schema.update-precondition",
                logical_references=(current.ref.canonical,),
                resource_ref=current.ref.schema_id,
            )
        return self.publish(
            catalog=catalog,
            namespace=namespace,
            name=name,
            version=version,
            definition=definition,
            expected_revision=expected_revision,
            allow_breaking=allow_breaking,
        )

    def deprecate(
        self,
        *,
        catalog: CatalogName | str = CatalogName.STRUCTURED,
        namespace: str,
        name: str,
        version: str,
        expected_revision: int | None = None,
    ) -> PublishedSchema:
        return self._repository.deprecate_schema(
            SchemaReference(CatalogName(catalog), namespace, name, version),
            expected_revision=expected_revision,
        )


class ResourceAPI:
    def __init__(self, repository: InMemoryMetadataRepository) -> None:
        self._repository = repository

    def create(
        self,
        document: CollectionDocument,
        adapter: SemanticsAdapter,
        *,
        expected_revision: int | None = None,
    ) -> tuple[CollectionDocument, ActivationResult]:
        provisional = self._repository.create_collection(
            document,
            expected_revision=expected_revision,
        )
        target = self._repository.get_schema(provisional.active_schema_ref).document
        try:
            adapter.validate_definition(target, provisional)
            plan = adapter.plan_activation(provisional, None, target)
            result = adapter.apply_activation(plan)
            if result.plan_fingerprint != plan.fingerprint:
                raise ValueError("Adapter activation result does not match its plan")
        except Exception as exc:
            failed = replace(provisional, state=CollectionState.PROVISIONING_FAILED)
            self._repository.replace_collection(failed)
            raise ActivationFailed(
                f"Resource activation failed: {type(exc).__name__}",
                requirement="resource.activation",
                logical_references=(provisional.ref.canonical, target.ref.canonical),
                resource_ref=provisional.ref.canonical,
            ) from exc
        active = replace(provisional, state=CollectionState.ACTIVE)
        self._repository.replace_collection(active)
        return active, result

    def read(self, reference: ResourceReference) -> CollectionDocument:
        return self._repository.get_collection(reference)


def resolve_all_neighbors(
    snapshot: RegistrySnapshot,
    start: ResourceReference,
    *,
    max_depth: int = 1,
    visible: Callable[[ResourceReference], bool] | None = None,
    binding_of: Callable[[ResourceReference], str] | None = None,
) -> TraversalResolution:
    if isinstance(max_depth, bool) or not 1 <= max_depth <= 32:
        raise ValueError("all_neighbors maxDepth must be between 1 and 32")
    start_ref = ResourceReference.parse(start, catalog=CatalogName.STRUCTURED)
    visible = visible or (lambda _ref: True)
    resources = {
        item.ref: item for item in snapshot.resources if item.state is CollectionState.ACTIVE
    }
    if start_ref not in resources:
        raise ResourceNotFound(
            f"Resource {start_ref.canonical} is not active in the registry snapshot",
            requirement="traversal.start-active",
            logical_references=(start_ref.canonical,),
            resource_ref=start_ref.canonical,
        )
    schemas = {item.ref: item.document for item in snapshot.schemas}
    relations: list[tuple[ResourceReference, RelationProfile]] = []
    for resource in resources.values():
        schema = schemas.get(resource.active_schema_ref)
        if schema is None:
            continue
        profile = schema.profile
        if isinstance(profile, RelationProfile):
            relations.append((resource.ref, profile))
    frontier = {start_ref}
    visited = {start_ref}
    selected: set[ResourceReference] = set()
    for _depth in range(max_depth):
        next_frontier: set[ResourceReference] = set()
        for collection in sorted(frontier):
            for relation_ref, profile in relations:
                neighbors = profile.neighbors(collection)
                if not neighbors or not visible(relation_ref):
                    continue
                selected.add(relation_ref)
                next_frontier.update(
                    item
                    for item in neighbors
                    if item in resources and item not in visited and visible(item)
                )
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    if binding_of is not None:
        binding = binding_of(start_ref)
        cross_binding = [item for item in selected | visited if binding_of(item) != binding]
        if cross_binding:
            raise CapabilityMismatch(
                "all_neighbors resolved relation Collections across Bindings",
                requirement="traversal.single-binding",
                logical_references=tuple(item.canonical for item in cross_binding),
                resource_ref=start_ref.canonical,
            )
    return TraversalResolution(start_ref, tuple(sorted(selected)), snapshot.fingerprint, max_depth)


class SemanticsSchemaProvider:
    """Core 1.0.0 schema-provider entry point for bootstrap and live metadata."""

    provider_id = "meridian.semantics"
    provider_contract_version = SEMANTICS_CONTRACT_VERSION

    def __init__(self, repository: InMemoryMetadataRepository | None = None) -> None:
        self._repository = repository

    def load(self) -> CoreResourceBundle:
        schema = SchemaDocument.from_definition(
            catalog="structured",
            namespace="meridian",
            name="registry_metadata",
            version="1.0.0",
            definition={
                "semanticKind": "relational",
                "fields": [
                    {"name": "kind", "logicalType": "string", "nullable": False},
                    {"name": "document", "logicalType": "json", "nullable": False},
                ],
                "identity": ["kind"],
            },
        )
        validate_schema(schema)
        return CoreResourceBundle(
            provider_id=self.provider_id,
            provider_version=__version__,
            provider_contract_version=self.provider_contract_version,
            namespaces=(
                CoreNamespaceDefinition("structured", "meridian", {"owner": "semantics"}),
                CoreNamespaceDefinition("cache", "meridian", {"owner": "semantics"}),
            ),
            schemas=(schema.to_core_definition(),),
            resources=(
                CoreResourceDefinition(
                    STRUCTURED_REGISTRY_REF.to_core(),
                    profile="metadata-registry",
                    schema=schema.ref.to_core(),
                ),
                CoreResourceDefinition(
                    CACHE_REGISTRY_REF.to_core(),
                    profile="metadata-registry",
                ),
            ),
            extensions={
                "design.hldRevision": 56,
                "design.catalogRevision": 70,
            },
        )

    def load_live(self) -> CoreResourceBundle:
        if self._repository is None:
            return CoreResourceBundle(
                provider_id="meridian.semantics.live",
                provider_version=__version__,
                provider_contract_version=self.provider_contract_version,
            )
        snapshot = self._repository.snapshot()
        namespaces = {
            (item.ref.catalog.value, item.ref.namespace)
            for item in snapshot.schemas
            if item.ref.namespace != "meridian"
        } | {
            (item.ref.catalog.value, item.ref.namespace)
            for item in snapshot.resources
            if item.ref.namespace != "meridian"
        }
        return CoreResourceBundle(
            provider_id="meridian.semantics.live",
            provider_version=__version__,
            provider_contract_version=self.provider_contract_version,
            namespaces=tuple(
                CoreNamespaceDefinition(catalog, namespace, {"source": "live"})
                for catalog, namespace in sorted(namespaces)
            ),
            schemas=tuple(item.document.to_core_definition() for item in snapshot.schemas),
            resources=tuple(
                CoreResourceDefinition(
                    item.ref.to_core(),
                    profile=item.semantic_profile,
                    schema=item.active_schema_ref.to_core(),
                    labels=item.labels,
                    extensions={"org.meridian.collection/state": item.state.value},
                )
                for item in snapshot.resources
            ),
            extensions={"registryFingerprint": snapshot.fingerprint},
        )


def _latest(
    values: Sequence[PublishedSchema],
    *,
    include_deprecated: bool,
) -> PublishedSchema | None:
    candidates = [
        item for item in values if include_deprecated or item.status is SchemaStatus.PUBLISHED
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: SemanticVersion.parse(cast(str, item.ref.version)))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("metadata repository clock must return timezone-aware datetimes")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = [
    "CACHE_REGISTRY_REF",
    "SEMANTICS_CONTRACT_VERSION",
    "STRUCTURED_REGISTRY_REF",
    "InMemoryMetadataRepository",
    "PublishResult",
    "PublishedSchema",
    "RegistrySnapshot",
    "ResourceAPI",
    "SchemaAPI",
    "SchemaStatus",
    "SemanticsSchemaProvider",
    "TraversalResolution",
    "resolve_all_neighbors",
]
