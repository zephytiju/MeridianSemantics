# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.metadata

import pytest

from meridian_storage import CatalogNotFound, Expression
from meridian_storage.semantics import (
    CacheCatalogProvider,
    CatalogName,
    InvalidDefinition,
    ResourceReference,
    StructuredCatalogProvider,
    TraversalResolution,
    cache_manifest,
    catalog_registry,
    discover_catalog,
    structured_manifest,
)


def test_manifests_and_installed_entry_points() -> None:
    structured = structured_manifest()
    cache = cache_manifest()
    assert structured.catalog_name == "structured"
    assert cache.catalog_name == "cache"
    assert structured.package_version == "1.0.0"
    assert structured.operation_for("query").read_only
    entry_points = importlib.metadata.entry_points()
    catalogs = {item.name: item for item in entry_points.select(group="meridian_storage.catalogs")}
    schemas = {item.name: item for item in entry_points.select(group="meridian_storage.schemas")}
    assert isinstance(catalogs["structured"].load()(), StructuredCatalogProvider)
    assert isinstance(catalogs["cache"].load()(), CacheCatalogProvider)
    assert schemas["semantics"].value.endswith(":SemanticsSchemaProvider")


def test_structured_surface_and_operation_normalization() -> None:
    provider = StructuredCatalogProvider()
    surface = provider.create_surface()
    expressions = [
        surface.publish_schema(
            namespace="example", name="customer", version="1.0.0", definition={}
        ),
        surface.create_resource(namespace="example", name="customers", schema={"version": "1.0.0"}),
        surface.put(resource="example.customers", data={"id": "1"}),
        surface.get(resource="example.customers", where={"id": "1"}),
        surface.patch(
            resource="example.customers",
            where={"id": "1"},
            changes={"name": "Ada"},
            expected_version=1,
        ),
        surface.delete(resource="example.customers", where={"id": "1"}),
        surface.query(
            resource="example.customers", select=("id",), order_by=({"field": "id"},), cursor="next"
        ),
        surface.search(
            resource="example.customers", query="ada", facets=("kind",), highlights=("name",)
        ),
        surface.aggregate(
            resource="example.customers", metrics=({"count": "*"},), group_by=("kind",)
        ),
        surface.traverse(
            resource="example.customers", start={"id": "1"}, relation_collections=("example.edges",)
        ),
    ]
    for expression in expressions:
        operation = provider.normalize(expression)
        assert operation.catalog == "structured"
        assert operation.operation_contract == f"meridian.structured.{expression.method}"
        assert operation.resources
        assert operation.read_only is provider.manifest().operation_for(expression.method).read_only
    assert provider.normalize(expressions[4]).idempotent
    assert not provider.normalize(expressions[5]).idempotent


def test_cache_surface_and_validation() -> None:
    provider = CacheCatalogProvider()
    surface = provider.create_surface()
    expressions = [
        surface.create_resource(namespace="example", name="sessions"),
        surface.get(resource="example.sessions", key="a"),
        surface.put(resource="example.sessions", key="a", value=1, ttl_ms=1000, source_version=2),
        surface.put_if_absent(resource="example.sessions", key="a", value=1),
        surface.compare_and_set(resource="example.sessions", key="a", expected_version=2, value=3),
        surface.delete(resource="example.sessions", key="a"),
        surface.invalidate(resource="example.sessions", selector={"prefix": "tenant:"}),
    ]
    assert all(provider.normalize(item).resources for item in expressions)
    assert provider.normalize(expressions[4]).idempotent
    with pytest.raises(InvalidDefinition, match="ttlMs"):
        provider.normalize(surface.put(resource="example.sessions", key="a", value=1, ttl_ms=0))


def test_all_neighbors_is_resolved_before_operation_serialization() -> None:
    start = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    edge = ResourceReference(CatalogName.STRUCTURED, "example", "edges")

    def resolver(reference: ResourceReference, depth: int) -> TraversalResolution:
        assert reference == start
        assert depth == 2
        return TraversalResolution(reference, (edge,), "sha256:" + "a" * 64, depth)

    provider = StructuredCatalogProvider(resolver)
    expression = provider.create_surface().traverse(
        resource=start.canonical,
        start={"id": "1"},
        all_neighbors=True,
        max_depth=2,
    )
    operation = provider.normalize(expression)
    assert [item.name for item in operation.resources] == ["customers", "edges"]
    assert operation.input["registryFingerprint"] == "sha256:" + "a" * 64
    with pytest.raises(InvalidDefinition, match="resolver"):
        StructuredCatalogProvider().normalize(expression)


def test_catalog_expression_rejections() -> None:
    provider = StructuredCatalogProvider()
    surface = provider.create_surface()
    with pytest.raises(ValueError, match="exactly one"):
        surface.traverse(
            resource="example.customers",
            start={"id": "1"},
            relation_collections=("example.edges",),
            all_neighbors=True,
        )
    with pytest.raises(InvalidDefinition, match="between"):
        provider.normalize(surface.query(resource="example.customers", limit=0))
    with pytest.raises(InvalidDefinition, match="provider"):
        provider.normalize(Expression("cache", "get", {"resource": "example.x", "key": "a"}))
    with pytest.raises(InvalidDefinition, match="unsupported"):
        provider.normalize(Expression("structured", "native_query", {}))
    with pytest.raises(InvalidDefinition, match="logical Resource"):
        provider.normalize(surface.get(resource="bad", where={}))
    with pytest.raises(InvalidDefinition, match="unknown or missing"):
        provider.normalize(Expression("structured", "get", {"resource": "example.customers"}))
    with pytest.raises(InvalidDefinition, match="array of field names"):
        provider.normalize(
            Expression(
                "structured",
                "query",
                {
                    "resource": "example.customers",
                    "where": {},
                    "select": "id",
                    "orderBy": [],
                    "limit": 50,
                },
            )
        )
    with pytest.raises(InvalidDefinition, match="non-negative"):
        provider.normalize(
            Expression(
                "structured",
                "publish_schema",
                {
                    "namespace": "example",
                    "name": "customer",
                    "version": "1.0.0",
                    "definition": {},
                    "allowBreaking": False,
                    "expectedRegistryRevision": -1,
                },
            )
        )


def test_catalog_registry_is_exact_and_discoverable() -> None:
    registry = catalog_registry()
    assert tuple(registry) == ("structured", "object", "cache", "evidence", "streaming")
    assert registry["structured"].owning_package == "meridian-storage-semantics"
    assert registry["object"].owning_package == "meridian-storage-object-common"
    assert registry["streaming"].status.value == "accepted-pending"
    assert discover_catalog("cache").name is CatalogName.CACHE
    with pytest.raises(CatalogNotFound):
        discover_catalog("query")
