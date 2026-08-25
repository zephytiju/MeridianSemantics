# SPDX-License-Identifier: Apache-2.0
"""Core 1.0.0 Catalog providers for structured and cache Expression surfaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

from meridian_storage import (
    CatalogManifest,
    CatalogNotFound,
    Expression,
    Operation,
    OperationContract,
)
from meridian_storage import (
    ResourceRef as CoreResourceRef,
)

from ._version import __version__
from .canonical import JsonValue
from .errors import InvalidDefinition
from .publication import (
    CACHE_REGISTRY_REF,
    SEMANTICS_CONTRACT_VERSION,
    STRUCTURED_REGISTRY_REF,
    TraversalResolution,
)
from .resources import (
    CatalogDescriptor,
    CatalogName,
    CatalogStatus,
    ResourceReference,
    SchemaReference,
)

RelationResolver = Callable[[ResourceReference, int], TraversalResolution]

_STRUCTURED_OPERATIONS: Mapping[str, tuple[bool, str]] = MappingProxyType(
    {
        "aggregate": (True, "always"),
        "create_resource": (False, "always"),
        "delete": (False, "conditional"),
        "get": (True, "always"),
        "patch": (False, "conditional"),
        "publish_schema": (False, "always"),
        "put": (False, "always"),
        "query": (True, "always"),
        "search": (True, "always"),
        "traverse": (True, "always"),
    }
)
_CACHE_OPERATIONS: Mapping[str, tuple[bool, str]] = MappingProxyType(
    {
        "compare_and_set": (False, "conditional"),
        "create_resource": (False, "always"),
        "delete": (False, "always"),
        "get": (True, "always"),
        "invalidate": (False, "always"),
        "put": (False, "always"),
        "put_if_absent": (False, "always"),
    }
)


def structured_manifest() -> CatalogManifest:
    return _manifest("structured", _STRUCTURED_OPERATIONS)


def cache_manifest() -> CatalogManifest:
    return _manifest("cache", _CACHE_OPERATIONS)


def _manifest(
    catalog: str,
    definitions: Mapping[str, tuple[bool, str]],
) -> CatalogManifest:
    return CatalogManifest(
        catalog_name=catalog,
        package_name="meridian-storage-semantics",
        package_version=__version__,
        catalog_contract_version=SEMANTICS_CONTRACT_VERSION,
        operations=tuple(
            OperationContract(
                method=method,
                operation_contract=f"meridian.{catalog}.{method}",
                operation_version="1.0.0",
                read_only=read_only,
                idempotency=idempotency,
            )
            for method, (read_only, idempotency) in definitions.items()
        ),
        extensions={
            "design.hldRevision": 56,
            "design.catalogRevision": 70,
            "schemaFormat": "meridian.schema.v1",
        },
    )


class StructuredCatalogSurface:
    catalog_name = "structured"

    def publish_schema(
        self,
        *,
        namespace: str,
        name: str,
        version: str,
        definition: Mapping[str, object],
        expected_registry_revision: int | None = None,
        allow_breaking: bool = False,
    ) -> Expression:
        arguments: dict[str, Any] = {
            "namespace": namespace,
            "name": name,
            "version": version,
            "definition": dict(definition),
            "allowBreaking": allow_breaking,
        }
        if expected_registry_revision is not None:
            arguments["expectedRegistryRevision"] = expected_registry_revision
        return self._expression("publish_schema", arguments)

    def create_resource(
        self,
        *,
        namespace: str,
        name: str,
        schema: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> Expression:
        return self._expression(
            "create_resource",
            {
                "namespace": namespace,
                "name": name,
                "schema": dict(schema),
                "options": dict(options or {}),
            },
        )

    def put(
        self,
        *,
        resource: str | Mapping[str, object],
        data: Mapping[str, object],
        expected_version: str | int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {"resource": resource, "data": dict(data)}
        if expected_version is not None:
            arguments["expectedVersion"] = expected_version
        return self._expression("put", arguments)

    def get(
        self,
        *,
        resource: str | Mapping[str, object],
        where: Mapping[str, object],
    ) -> Expression:
        return self._expression("get", {"resource": resource, "where": dict(where)})

    def patch(
        self,
        *,
        resource: str | Mapping[str, object],
        where: Mapping[str, object],
        changes: Mapping[str, object],
        expected_version: str | int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {
            "resource": resource,
            "where": dict(where),
            "changes": dict(changes),
        }
        if expected_version is not None:
            arguments["expectedVersion"] = expected_version
        return self._expression("patch", arguments)

    def delete(
        self,
        *,
        resource: str | Mapping[str, object],
        where: Mapping[str, object],
        expected_version: str | int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {"resource": resource, "where": dict(where)}
        if expected_version is not None:
            arguments["expectedVersion"] = expected_version
        return self._expression("delete", arguments)

    def query(
        self,
        *,
        resource: str | Mapping[str, object],
        where: Mapping[str, object] | None = None,
        select: Sequence[str] = (),
        order_by: Sequence[Mapping[str, object]] = (),
        limit: int = 50,
        cursor: str | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {
            "resource": resource,
            "where": dict(where or {}),
            "select": list(select),
            "orderBy": [dict(item) for item in order_by],
            "limit": limit,
        }
        if cursor is not None:
            arguments["cursor"] = cursor
        return self._expression("query", arguments)

    def search(
        self,
        *,
        resource: str | Mapping[str, object],
        query: str | Mapping[str, object],
        where: Mapping[str, object] | None = None,
        facets: Sequence[str] = (),
        highlights: Sequence[str] = (),
        limit: int = 50,
        cursor: str | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {
            "resource": resource,
            "query": query,
            "where": dict(where or {}),
            "facets": list(facets),
            "highlights": list(highlights),
            "limit": limit,
        }
        if cursor is not None:
            arguments["cursor"] = cursor
        return self._expression("search", arguments)

    def aggregate(
        self,
        *,
        resource: str | Mapping[str, object],
        metrics: Sequence[Mapping[str, object]],
        group_by: Sequence[str] = (),
        where: Mapping[str, object] | None = None,
    ) -> Expression:
        return self._expression(
            "aggregate",
            {
                "resource": resource,
                "metrics": [dict(item) for item in metrics],
                "groupBy": list(group_by),
                "where": dict(where or {}),
            },
        )

    def traverse(
        self,
        *,
        resource: str | Mapping[str, object],
        start: Mapping[str, object],
        relation_collections: Sequence[str | Mapping[str, object]] = (),
        all_neighbors: bool = False,
        max_depth: int = 1,
    ) -> Expression:
        if bool(relation_collections) == all_neighbors:
            raise ValueError(
                "traverse requires exactly one of relation_collections or all_neighbors"
            )
        return self._expression(
            "traverse",
            {
                "resource": resource,
                "start": dict(start),
                "relationCollections": list(relation_collections),
                "allNeighbors": all_neighbors,
                "maxDepth": max_depth,
            },
        )

    def _expression(self, method: str, arguments: Mapping[str, Any]) -> Expression:
        return Expression(self.catalog_name, method, cast(Mapping[str, JsonValue], arguments))


class CacheCatalogSurface:
    catalog_name = "cache"

    def create_resource(
        self,
        *,
        namespace: str,
        name: str,
        options: Mapping[str, object] | None = None,
    ) -> Expression:
        return self._expression(
            "create_resource",
            {"namespace": namespace, "name": name, "options": dict(options or {})},
        )

    def get(self, *, resource: str | Mapping[str, object], key: object) -> Expression:
        return self._expression("get", {"resource": resource, "key": key})

    def put(
        self,
        *,
        resource: str | Mapping[str, object],
        key: object,
        value: object,
        ttl_ms: int | None = None,
        source_version: str | int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {"resource": resource, "key": key, "value": value}
        if ttl_ms is not None:
            arguments["ttlMs"] = ttl_ms
        if source_version is not None:
            arguments["sourceVersion"] = source_version
        return self._expression("put", arguments)

    def put_if_absent(
        self,
        *,
        resource: str | Mapping[str, object],
        key: object,
        value: object,
        ttl_ms: int | None = None,
        source_version: str | int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {"resource": resource, "key": key, "value": value}
        if ttl_ms is not None:
            arguments["ttlMs"] = ttl_ms
        if source_version is not None:
            arguments["sourceVersion"] = source_version
        return self._expression("put_if_absent", arguments)

    def compare_and_set(
        self,
        *,
        resource: str | Mapping[str, object],
        key: object,
        expected_version: str | int,
        value: object,
        ttl_ms: int | None = None,
    ) -> Expression:
        arguments: dict[str, Any] = {
            "resource": resource,
            "key": key,
            "expectedVersion": expected_version,
            "value": value,
        }
        if ttl_ms is not None:
            arguments["ttlMs"] = ttl_ms
        return self._expression("compare_and_set", arguments)

    def delete(self, *, resource: str | Mapping[str, object], key: object) -> Expression:
        return self._expression("delete", {"resource": resource, "key": key})

    def invalidate(
        self,
        *,
        resource: str | Mapping[str, object],
        selector: Mapping[str, object],
    ) -> Expression:
        return self._expression("invalidate", {"resource": resource, "selector": dict(selector)})

    def _expression(self, method: str, arguments: Mapping[str, Any]) -> Expression:
        return Expression(self.catalog_name, method, cast(Mapping[str, JsonValue], arguments))


class StructuredCatalogProvider:
    catalog_name = "structured"

    def __init__(self, relation_resolver: RelationResolver | None = None) -> None:
        self._manifest = structured_manifest()
        self._resolver = relation_resolver

    def manifest(self) -> CatalogManifest:
        return self._manifest

    def create_surface(self) -> StructuredCatalogSurface:
        return StructuredCatalogSurface()

    def normalize(self, expression: Expression) -> Operation:
        return _normalize(expression, self._manifest, relation_resolver=self._resolver)


class CacheCatalogProvider:
    catalog_name = "cache"

    def __init__(self) -> None:
        self._manifest = cache_manifest()

    def manifest(self) -> CatalogManifest:
        return self._manifest

    def create_surface(self) -> CacheCatalogSurface:
        return CacheCatalogSurface()

    def normalize(self, expression: Expression) -> Operation:
        return _normalize(expression, self._manifest)


def _normalize(
    expression: Expression,
    manifest: CatalogManifest,
    *,
    relation_resolver: RelationResolver | None = None,
) -> Operation:
    if expression.catalog != manifest.catalog_name:
        raise InvalidDefinition(
            "Expression Catalog does not match its provider",
            requirement="expression.catalog",
        )
    try:
        contract = manifest.operation_for(expression.method)
    except KeyError as exc:
        raise InvalidDefinition(
            f"unsupported {manifest.catalog_name} Expression method {expression.method!r}",
            requirement="expression.method",
        ) from exc
    input_value: dict[str, Any] = dict(expression.arguments)
    resources: tuple[CoreResourceRef, ...]
    if expression.method in {"publish_schema", "create_resource"}:
        registry = (
            STRUCTURED_REGISTRY_REF if manifest.catalog_name == "structured" else CACHE_REGISTRY_REF
        )
        resources = (registry.to_core(),)
        _validate_registry_arguments(manifest.catalog_name, expression.method, input_value)
    elif expression.method == "traverse":
        _validate_traverse_arguments(input_value)
        start = _parse_resource(input_value.get("resource"), "structured")
        raw_relations = input_value.get("relationCollections", ())
        if not isinstance(raw_relations, Sequence) or isinstance(raw_relations, (str, bytes)):
            raise InvalidDefinition(
                "traverse relationCollections must be an array",
                requirement="traverse.relations",
            )
        relations = tuple(_parse_resource(item, "structured") for item in raw_relations)
        if input_value.get("allNeighbors") is True:
            if relation_resolver is None:
                raise InvalidDefinition(
                    "all_neighbors requires the Semantics registry resolver during planning",
                    requirement="traverse.registry-resolution",
                    logical_references=(str(start),),
                    resource_ref=str(start),
                )
            max_depth = input_value.get("maxDepth", 1)
            if isinstance(max_depth, bool) or not isinstance(max_depth, int):
                raise InvalidDefinition("maxDepth must be an integer")
            resolved = relation_resolver(
                ResourceReference.parse(start),
                max_depth,
            )
            relations = tuple(item.to_core() for item in resolved.relation_collections)
            input_value["resolvedRelations"] = [
                item.to_dict() for item in resolved.relation_collections
            ]
            input_value["registryFingerprint"] = resolved.registry_fingerprint
        resources = _unique_resources((start, *relations))
    else:
        resources = (_parse_resource(input_value.get("resource"), manifest.catalog_name),)
        _validate_data_arguments(manifest.catalog_name, expression.method, input_value)
    if contract.idempotency == "always":
        idempotent = True
    elif contract.idempotency == "never":
        idempotent = False
    else:
        idempotent = any(
            input_value.get(key) is not None for key in ("expectedVersion", "ifMatch", "createOnly")
        )
    return Operation(
        catalog=manifest.catalog_name,
        operation_contract=contract.operation_contract,
        operation_version=contract.operation_version,
        resources=resources,
        input=cast(Mapping[str, JsonValue], input_value),
        requirements=(contract.requirement,),
        read_only=contract.read_only,
        idempotent=idempotent,
    )


def _parse_resource(value: object, catalog: str) -> CoreResourceRef:
    try:
        if isinstance(value, str):
            return CoreResourceRef.parse(value, catalog=catalog)
        if isinstance(value, Mapping):
            return ResourceReference.parse(value, catalog=catalog).to_core()
    except (TypeError, ValueError) as exc:
        raise InvalidDefinition(
            f"invalid logical Resource reference: {exc}",
            requirement="operation.resource",
        ) from exc
    raise InvalidDefinition("Operation requires a logical Resource reference")


def _validate_registry_arguments(catalog: str, method: str, value: Mapping[str, object]) -> None:
    if method == "publish_schema":
        required = {"namespace", "name", "version", "definition", "allowBreaking"}
        allowed = required | {"expectedRegistryRevision"}
    elif catalog == "structured":
        required = {"namespace", "name", "schema", "options"}
        allowed = required
    else:
        required = {"namespace", "name", "options"}
        allowed = required
    if required - set(value) or set(value) - allowed:
        raise InvalidDefinition(
            f"{catalog}.{method} contains unknown or missing arguments",
            requirement="expression.arguments",
        )
    namespace = value["namespace"]
    name = value["name"]
    if not isinstance(namespace, str) or not isinstance(name, str):
        raise InvalidDefinition(
            f"{catalog}.{method} namespace and name must be strings",
            requirement="expression.arguments",
        )
    try:
        ResourceReference(CatalogName(catalog), namespace, name)
    except (TypeError, ValueError) as exc:
        raise InvalidDefinition(
            f"{catalog}.{method} has an invalid logical Resource name",
            requirement="operation.resource",
        ) from exc
    if method == "publish_schema":
        version = value["version"]
        if not isinstance(version, str) or not isinstance(value["definition"], Mapping):
            raise InvalidDefinition(
                "publish_schema version must be a string and definition must be an object",
                requirement="expression.arguments",
            )
        try:
            SchemaReference(CatalogName(catalog), namespace, name, version).exact()
        except (TypeError, ValueError) as exc:
            raise InvalidDefinition(
                "publish_schema has an invalid exact Schema address",
                requirement="schema.canonical",
            ) from exc
        if not isinstance(value["allowBreaking"], bool):
            raise InvalidDefinition(
                "publish_schema allowBreaking must be boolean",
                requirement="expression.arguments",
            )
        expected_revision = value.get("expectedRegistryRevision")
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise InvalidDefinition(
                "expectedRegistryRevision must be a non-negative integer",
                requirement="registry.compare-and-set",
            )
    else:
        if not isinstance(value["options"], Mapping):
            raise InvalidDefinition(
                f"{catalog}.{method} options must be an object",
                requirement="expression.arguments",
            )
        if catalog == "structured" and not isinstance(value["schema"], Mapping):
            raise InvalidDefinition(
                "structured.create_resource schema must be an object",
                requirement="expression.arguments",
            )


def _validate_traverse_arguments(value: Mapping[str, object]) -> None:
    required = {"resource", "start", "relationCollections", "allNeighbors", "maxDepth"}
    if set(value) != required:
        raise InvalidDefinition(
            "structured.traverse contains unknown or missing arguments",
            requirement="expression.arguments",
        )
    if not isinstance(value["start"], Mapping):
        raise InvalidDefinition("traverse start must be an object", requirement="traverse.start")
    all_neighbors = value["allNeighbors"]
    if not isinstance(all_neighbors, bool):
        raise InvalidDefinition(
            "traverse allNeighbors must be boolean",
            requirement="traverse.relations",
        )
    max_depth = value["maxDepth"]
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 32:
        raise InvalidDefinition(
            "traverse maxDepth must be between 1 and 32",
            requirement="traverse.depth",
        )
    relations = value["relationCollections"]
    if not isinstance(relations, Sequence) or isinstance(relations, (str, bytes)):
        raise InvalidDefinition(
            "traverse relationCollections must be an array",
            requirement="traverse.relations",
        )
    if bool(relations) == all_neighbors:
        raise InvalidDefinition(
            "traverse requires exactly one of relationCollections or allNeighbors",
            requirement="traverse.relations",
        )


def _validate_data_arguments(
    catalog: str,
    method: str,
    value: Mapping[str, object],
) -> None:
    shapes: Mapping[tuple[str, str], tuple[set[str], set[str]]] = {
        ("structured", "put"): ({"resource", "data"}, {"expectedVersion"}),
        ("structured", "get"): ({"resource", "where"}, set()),
        ("structured", "patch"): (
            {"resource", "where", "changes"},
            {"expectedVersion"},
        ),
        ("structured", "delete"): ({"resource", "where"}, {"expectedVersion"}),
        ("structured", "query"): (
            {"resource", "where", "select", "orderBy", "limit"},
            {"cursor"},
        ),
        ("structured", "search"): (
            {"resource", "query", "where", "facets", "highlights", "limit"},
            {"cursor"},
        ),
        ("structured", "aggregate"): (
            {"resource", "metrics", "groupBy", "where"},
            set(),
        ),
        ("cache", "get"): ({"resource", "key"}, set()),
        ("cache", "put"): (
            {"resource", "key", "value"},
            {"ttlMs", "sourceVersion"},
        ),
        ("cache", "put_if_absent"): (
            {"resource", "key", "value"},
            {"ttlMs", "sourceVersion"},
        ),
        ("cache", "compare_and_set"): (
            {"resource", "key", "expectedVersion", "value"},
            {"ttlMs"},
        ),
        ("cache", "delete"): ({"resource", "key"}, set()),
        ("cache", "invalidate"): ({"resource", "selector"}, set()),
    }
    required, optional = shapes[(catalog, method)]
    if required - set(value) or set(value) - required - optional:
        raise InvalidDefinition(
            f"{catalog}.{method} contains unknown or missing arguments",
            requirement="expression.arguments",
        )
    for key in ("data", "where", "changes", "selector"):
        if key in value and not isinstance(value[key], Mapping):
            raise InvalidDefinition(
                f"{catalog}.{method} {key} must be an object",
                requirement="expression.arguments",
            )
    for key in ("select", "facets", "highlights", "groupBy"):
        if key in value and (
            not isinstance(value[key], Sequence)
            or isinstance(value[key], (str, bytes))
            or any(not isinstance(item, str) for item in cast(Sequence[object], value[key]))
        ):
            raise InvalidDefinition(
                f"{catalog}.{method} {key} must be an array of field names",
                requirement="expression.arguments",
            )
    for key in ("orderBy", "metrics"):
        if key in value and (
            not isinstance(value[key], Sequence)
            or isinstance(value[key], (str, bytes))
            or any(not isinstance(item, Mapping) for item in cast(Sequence[object], value[key]))
        ):
            raise InvalidDefinition(
                f"{catalog}.{method} {key} must be an array of objects",
                requirement="expression.arguments",
            )
    expected = value.get("expectedVersion")
    if expected is not None and (
        isinstance(expected, bool) or not isinstance(expected, (str, int))
    ):
        raise InvalidDefinition(
            "expectedVersion must be a string or integer",
            requirement="operation.precondition",
        )
    cursor = value.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise InvalidDefinition(
            "query cursor must be a string",
            requirement="expression.arguments",
        )
    if method == "search" and not isinstance(value["query"], (str, Mapping)):
        raise InvalidDefinition(
            "search query must be a string or mapping",
            requirement="expression.arguments",
        )
    source_version = value.get("sourceVersion")
    if source_version is not None and (
        isinstance(source_version, bool) or not isinstance(source_version, (str, int))
    ):
        raise InvalidDefinition(
            "sourceVersion must be a string or integer",
            requirement="expression.arguments",
        )
    if method in {"query", "search"}:
        limit = value.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise InvalidDefinition(
                f"{method} limit must be between 1 and 10000",
                requirement="query.limit",
            )
    if "ttlMs" in value:
        ttl = value["ttlMs"]
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise InvalidDefinition("ttlMs must be a positive integer", requirement="cache.ttl")


def _unique_resources(values: Sequence[CoreResourceRef]) -> tuple[CoreResourceRef, ...]:
    result = tuple(sorted(set(values)))
    if not result:
        raise InvalidDefinition("Operation Resource set cannot be empty")
    return result


_CATALOG_REGISTRY: Mapping[str, CatalogDescriptor] = MappingProxyType(
    {
        "structured": CatalogDescriptor(
            CatalogName.STRUCTURED,
            CatalogStatus.V1,
            "meridian-storage-semantics",
            ("Collection",),
            ("Record",),
            tuple(sorted(_STRUCTURED_OPERATIONS)),
        ),
        "object": CatalogDescriptor(
            CatalogName.OBJECT,
            CatalogStatus.V1,
            "meridian-storage-object-common",
            ("Object",),
            ("Object",),
            (
                "create_resource",
                "delete",
                "get",
                "list",
                "publish_schema",
                "put",
                "read_range",
                "stat",
            ),
        ),
        "cache": CatalogDescriptor(
            CatalogName.CACHE,
            CatalogStatus.V1,
            "meridian-storage-semantics",
            ("Cache",),
            ("CacheEntry",),
            tuple(sorted(_CACHE_OPERATIONS)),
        ),
        "evidence": CatalogDescriptor(
            CatalogName.EVIDENCE,
            CatalogStatus.V1,
            "meridian-storage-evidence",
            ("Telemetry", "Audit", "Lineage"),
            ("Log", "Span", "Metric", "Audit", "Lineage", "Provenance"),
            ("append", "create_resource", "publish_schema", "query"),
        ),
        "streaming": CatalogDescriptor(
            CatalogName.STREAMING,
            CatalogStatus.ACCEPTED_PENDING,
            "meridian-storage-streaming",
            ("Stream", "Subscription", "ConsumerGroup"),
            ("Event", "Delivery", "Position", "Cursor"),
            (
                "acknowledge",
                "create_resource",
                "negative_acknowledge",
                "poll",
                "publish",
                "publish_batch",
                "publish_schema",
                "read_range",
                "subscribe",
            ),
        ),
    }
)


def catalog_registry() -> Mapping[str, CatalogDescriptor]:
    return _CATALOG_REGISTRY


def discover_catalog(name: str) -> CatalogDescriptor:
    try:
        return _CATALOG_REGISTRY[name]
    except KeyError as exc:
        raise CatalogNotFound(name) from exc


__all__ = [
    "CacheCatalogProvider",
    "CacheCatalogSurface",
    "RelationResolver",
    "StructuredCatalogProvider",
    "StructuredCatalogSurface",
    "cache_manifest",
    "catalog_registry",
    "discover_catalog",
    "structured_manifest",
]
