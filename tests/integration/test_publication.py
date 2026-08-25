# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import replace

import pytest
from meridian_storage.registry import ResourceBundle

from meridian_storage.semantics import (
    ActivationFailed,
    ActivationPlan,
    ActivationResult,
    CatalogName,
    CollectionDocument,
    CollectionState,
    IncompatibleSchema,
    IndexDefinition,
    InMemoryMetadataRepository,
    LogicalKind,
    RegistryRevisionConflict,
    ResourceAlreadyExists,
    ResourceAPI,
    ResourceNotFound,
    ResourceReference,
    SchemaAPI,
    SchemaStatus,
    SchemaVersionConflict,
    SemanticKind,
    SemanticsAdapter,
    SemanticsSchemaProvider,
    resolve_all_neighbors,
)
from tests.support import FINGERPRINT, NOW, field, schema, schema_definition


@pytest.fixture
def repository() -> InMemoryMetadataRepository:
    return InMemoryMetadataRepository(clock=lambda: NOW)


def test_schema_publish_get_list_deprecate_and_revision(
    repository: InMemoryMetadataRepository,
) -> None:
    first = repository.publish_schema(schema())
    assert first.registry_revision == 1
    assert not first.idempotent
    duplicate = repository.publish_schema(schema(), expected_revision=1)
    assert duplicate.idempotent
    assert duplicate.registry_revision == 1
    second = repository.publish_schema(schema("1.1.0"), expected_revision=1)
    assert second.compatibility is not None
    assert repository.get_schema(replace(schema().ref, version=None)).ref.version == "1.1.0"
    assert [item.ref.version for item in repository.list_schema_versions(schema().ref)] == [
        "1.0.0",
        "1.1.0",
    ]
    deprecated = repository.deprecate_schema(schema().ref.exact(), expected_revision=2)
    assert deprecated.status is SchemaStatus.DEPRECATED
    assert deprecated.deprecated_at == "2026-08-25T12:34:56.123456Z"
    assert repository.deprecate_schema(schema().ref.exact()).status is SchemaStatus.DEPRECATED
    with pytest.raises(ResourceNotFound):
        repository.get_schema(schema().ref.exact())
    assert repository.get_schema(schema().ref.exact(), include_deprecated=True) == deprecated


def test_schema_immutability_monotonicity_compatibility_and_revision(
    repository: InMemoryMetadataRepository,
) -> None:
    repository.publish_schema(schema())
    conflicting = schema(fields=(field("id"), field("different", nullable=True)))
    with pytest.raises(SchemaVersionConflict, match="fingerprint"):
        repository.publish_schema(conflicting)
    with pytest.raises(SchemaVersionConflict, match="greater"):
        repository.publish_schema(schema("0.9.0"))
    breaking = schema("2.0.0", fields=(field("id"), field("required")))
    with pytest.raises(IncompatibleSchema):
        repository.publish_schema(breaking)
    assert repository.publish_schema(breaking, allow_breaking=True).compatibility.breaking  # type: ignore[union-attr]
    with pytest.raises(RegistryRevisionConflict, match="observed"):
        repository.publish_schema(schema("3.0.0"), expected_revision=0)


def test_schema_api_mapping_operations(repository: InMemoryMetadataRepository) -> None:
    api = SchemaAPI(repository)
    result = api.create(
        namespace="example",
        name="customer",
        version="1.0.0",
        definition=schema_definition(),
        expected_revision=0,
    )
    assert result.publication.ref.version == "1.0.0"
    assert (
        api.get(namespace="example", name="customer").fingerprint == result.publication.fingerprint
    )
    assert len(api.list_versions(namespace="example", name="customer")) == 1
    updated = api.update(
        namespace="example",
        name="customer",
        current_version="1.0.0",
        version="1.1.0",
        definition=schema_definition(),
        expected_revision=1,
    )
    assert updated.publication.ref.version == "1.1.0"
    with pytest.raises(RegistryRevisionConflict, match="active Schema"):
        api.update(
            namespace="example",
            name="customer",
            current_version="1.0.0",
            version="1.2.0",
            definition=schema_definition(),
        )
    api.deprecate(namespace="example", name="customer", version="1.0.0")
    with pytest.raises(Exception, match="requires semanticKind"):
        api.publish(namespace="example", name="bad", version="1.0.0", definition={})


class _Adapter:
    def __init__(self, *, fail: bool = False, mismatch: bool = False) -> None:
        self.fail = fail
        self.mismatch = mismatch

    def validate_definition(self, schema: object, resource: object = None) -> None:
        if self.fail:
            raise RuntimeError("unavailable")

    def plan_activation(
        self, resource: CollectionDocument, current_schema: object, target_schema: object
    ) -> ActivationPlan:
        return ActivationPlan(
            resource.ref,
            resource.active_schema_ref,
            steps=({"kind": "register-schema"},),
            requirements=("logical-schema",),
        )

    def apply_activation(self, plan: ActivationPlan) -> ActivationResult:
        return ActivationResult(
            FINGERPRINT if self.mismatch else plan.fingerprint,
            FINGERPRINT,
            "42",
            {"adapter": "fake"},
        )

    def read_registry_revision(self) -> str:
        return "42"

    def encode_value(self, schema: object, value: object) -> object:
        return value

    def decode_value(self, schema: object, value: object) -> object:
        return value

    def export_logical(self, resources: object) -> dict[str, object]:
        return {}

    def import_logical(self, payload: object) -> None:
        return None


def _collection(name: str = "customers", *, schema_name: str = "customer") -> CollectionDocument:
    return CollectionDocument(
        ResourceReference(CatalogName.STRUCTURED, "example", name),
        schema(name=schema_name).ref,
        "relational",
    )


def test_resource_activation_success_duplicate_and_failure(
    repository: InMemoryMetadataRepository,
) -> None:
    repository.publish_schema(schema())
    api = ResourceAPI(repository)
    assert isinstance(_Adapter(), SemanticsAdapter)
    active, result = api.create(_collection(), _Adapter(), expected_revision=1)
    assert active.state is CollectionState.ACTIVE
    assert result.registry_revision == "42"
    assert api.read(active.ref) == active
    with pytest.raises(ResourceAlreadyExists):
        api.create(_collection(), _Adapter())

    failed_collection = _collection("failed")
    with pytest.raises(ActivationFailed) as raised:
        api.create(failed_collection, _Adapter(fail=True))
    assert raised.value.__cause__ is not None
    assert api.read(failed_collection.ref).state is CollectionState.PROVISIONING_FAILED

    mismatch_collection = _collection("mismatch")
    with pytest.raises(ActivationFailed):
        api.create(mismatch_collection, _Adapter(mismatch=True))


def test_registry_snapshot_and_core_schema_provider(
    repository: InMemoryMetadataRepository,
) -> None:
    repository.publish_schema(schema())
    provisional = repository.create_collection(_collection())
    repository.replace_collection(replace(provisional, state=CollectionState.ACTIVE))
    snapshot = repository.snapshot()
    assert snapshot.revision == 3
    assert snapshot.fingerprint.startswith("sha256:")
    assert snapshot.to_dict()["resources"][0]["state"] == "ACTIVE"  # type: ignore[index]
    provider = SemanticsSchemaProvider(repository)
    bootstrap = provider.load()
    live = provider.load_live()
    assert isinstance(bootstrap, ResourceBundle)
    assert {item.ref.catalog for item in bootstrap.resources} == {"structured", "cache"}
    assert len(live.schemas) == 1
    assert len(live.resources) == 1
    empty = SemanticsSchemaProvider().load_live()
    assert empty.provider_id == "meridian.semantics.live"


def test_all_neighbors_resolution_visibility_depth_and_binding() -> None:
    repository = InMemoryMetadataRepository(clock=lambda: NOW)
    customers = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    orders = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    relation_ref = ResourceReference(CatalogName.STRUCTURED, "example", "customer_orders")
    relation_schema = schema(
        name="customer_order_edge",
        semantic_kind=SemanticKind.RELATION,
        fields=(
            field("id"),
            field("source", LogicalKind.RECORD_REF),
            field("target", LogicalKind.RECORD_REF),
        ),
        indexes=(
            IndexDefinition("source", "relation-endpoint", ("source",)),
            IndexDefinition("target", "relation-endpoint", ("target",)),
        ),
        profile={
            "kind": "relation",
            "sourceField": "source",
            "targetField": "target",
            "directed": False,
            "sourceCollections": [customers.to_dict()],
            "targetCollections": [orders.to_dict()],
        },
    )
    for document in (schema(), schema(name="order"), relation_schema):
        repository.publish_schema(document)
    for document in (
        CollectionDocument(customers, schema().ref, "relational", state=CollectionState.ACTIVE),
        CollectionDocument(
            orders, schema(name="order").ref, "relational", state=CollectionState.ACTIVE
        ),
        CollectionDocument(
            relation_ref, relation_schema.ref, "relation", state=CollectionState.ACTIVE
        ),
    ):
        created = repository.create_collection(document)
        repository.replace_collection(replace(created, state=CollectionState.ACTIVE))
    snapshot = repository.snapshot()
    resolved = resolve_all_neighbors(snapshot, customers)
    assert resolved.relation_collections == (relation_ref,)
    assert (
        resolve_all_neighbors(snapshot, customers, visible=lambda _ref: False).relation_collections
        == ()
    )
    with pytest.raises(ValueError, match="maxDepth"):
        resolve_all_neighbors(snapshot, customers, max_depth=0)
    with pytest.raises(Exception, match="Bindings"):
        resolve_all_neighbors(
            snapshot,
            customers,
            binding_of=lambda ref: "other" if ref == relation_ref else "main",
        )
    with pytest.raises(ResourceNotFound, match="not active"):
        resolve_all_neighbors(InMemoryMetadataRepository().snapshot(), customers)
