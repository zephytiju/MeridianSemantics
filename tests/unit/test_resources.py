# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from meridian_storage import ResourceRef as CoreResourceRef
from meridian_storage import SchemaRef as CoreSchemaRef
from meridian_storage.semantics import (
    ActivationFailed,
    CacheEntry,
    CatalogName,
    CollectionDocument,
    CollectionState,
    InvalidDefinition,
    ObjectMetadata,
    ObjectReference,
    Record,
    RecordReference,
    ResourceReference,
    SchemaReference,
)
from meridian_storage.semantics.resources import normalize_timestamp, validate_base64url
from tests.support import DIGEST, FINGERPRINT, NOW, NOW_TEXT


def test_resource_reference_forms_and_core_bridge() -> None:
    ref = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    assert ref.logical_name == "example.customers"
    assert ref.canonical == "structured:example.customers"
    assert str(ref) == ref.canonical
    assert ResourceReference.parse(ref) is ref
    assert ResourceReference.parse("structured:example.customers") == ref
    assert ResourceReference.parse("example.customers", catalog="structured") == ref
    assert ResourceReference.parse("customers", catalog="structured", namespace="example") == ref
    assert ResourceReference.parse(ref.to_dict()) == ref
    assert ResourceReference.parse(CoreResourceRef("structured", "example", "customers")) == ref
    assert ref.to_core() == CoreResourceRef("structured", "example", "customers")


@pytest.mark.parametrize(
    "value,kwargs",
    [
        ("customers", {}),
        ({"catalog": "structured", "name": "customers"}, {}),
        ({"catalog": "structured", "namespace": "example", "name": "x", "extra": 1}, {}),
        (3, {}),
    ],
)
def test_resource_reference_rejects_invalid_forms(value: object, kwargs: dict[str, str]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ResourceReference.parse(value, **kwargs)  # type: ignore[arg-type]
    ref = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    with pytest.raises(ValueError, match="Catalog"):
        ResourceReference.parse(ref, catalog="cache")
    with pytest.raises(ValueError, match="namespace"):
        ResourceReference.parse(ref, namespace="other")


def test_schema_reference_exact_latest_and_core_bridge() -> None:
    latest = SchemaReference(CatalogName.STRUCTURED, "example", "customer")
    exact = SchemaReference(CatalogName.STRUCTURED, "example", "customer", "1.2.3")
    assert latest.canonical == "structured:example.customer"
    assert exact.canonical == "structured:example.customer@1.2.3"
    assert exact.schema_id == latest.schema_id
    assert exact.exact() is exact
    assert SchemaReference.parse(exact.to_dict()) == exact
    assert SchemaReference.parse(exact.to_core()) == exact
    assert exact.to_core() == CoreSchemaRef("structured", "example", "customer", "1.2.3")
    with pytest.raises(ValueError, match="exact"):
        latest.exact()
    with pytest.raises(ValueError, match="requires an exact"):
        latest.to_core()
    with pytest.raises(ValueError):
        SchemaReference(CatalogName.STRUCTURED, "example", "customer", "1")


def test_collection_document_is_canonical() -> None:
    collection = CollectionDocument(
        ResourceReference(CatalogName.STRUCTURED, "example", "customers"),
        SchemaReference(CatalogName.STRUCTURED, "example", "customer", "1.0.0"),
        "relational",
        labels={"tier": "gold"},
        extensions={"org.example/owner": "team-a"},
    )
    assert collection.state is CollectionState.PROVISIONING
    assert collection.to_dict()["id"] == "structured:example.customers"
    assert collection.to_dict()["labels"] == {"tier": "gold"}
    with pytest.raises(ValueError, match="Catalog"):
        CollectionDocument(
            ResourceReference(CatalogName.CACHE, "example", "customers"),
            collection.active_schema_ref,
            "relational",
        )
    with pytest.raises(ValueError, match="format"):
        CollectionDocument(
            collection.ref,
            collection.active_schema_ref,
            "relational",
            format_version="v2",
        )
    with pytest.raises(ValueError, match="semanticProfile"):
        CollectionDocument(
            collection.ref,
            collection.active_schema_ref,
            "ontology",
        )


def test_record_reference_and_record_wire_values() -> None:
    collection = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    reference = RecordReference(collection, ("tenant", 42))
    assert RecordReference.from_mapping(reference.to_dict()) == reference
    record = Record(
        collection,
        42,
        {"name": "Ada", "nested": {"x": 1}},
        3,
        NOW,
        NOW + timedelta(seconds=1),
    )
    assert record.to_dict()["createdAt"] == NOW_TEXT
    assert record.to_dict()["values"] == {"name": "Ada", "nested": {"x": 1}}
    with pytest.raises(ValueError, match="identity"):
        RecordReference(collection, {"bad": "mapping"})
    with pytest.raises(ValueError, match="identity"):
        RecordReference(collection, ())
    with pytest.raises(ValueError, match="recordVersion"):
        Record(collection, 1, {}, -1, NOW, NOW)
    with pytest.raises(ValueError, match="precede"):
        Record(collection, 1, {}, 1, NOW, NOW - timedelta(seconds=1))


def test_object_metadata_and_digest_binding() -> None:
    resource = ResourceReference(CatalogName.OBJECT, "example", "artifacts")
    reference = ObjectReference(resource, "build/output.tar", DIGEST)
    metadata = ObjectMetadata(
        reference,
        DIGEST,
        42,
        "application/gzip",
        NOW,
        creation_context={"build": 7},
        user_metadata={"owner": "team-a"},
        provenance={"source": "ci"},
    )
    assert metadata.to_dict()["objectRef"] == reference.to_dict()
    assert metadata.to_dict()["createdAt"] == NOW_TEXT
    with pytest.raises(ValueError, match="does not match"):
        ObjectMetadata(reference, "sha256:" + "c" * 64, 1, "text/plain", NOW)
    with pytest.raises(ValueError, match="mediaType"):
        ObjectMetadata(reference, DIGEST, 1, "text plain", NOW)
    with pytest.raises(ValueError, match="negative"):
        ObjectMetadata(reference, DIGEST, -1, "text/plain", NOW)
    with pytest.raises(ValueError, match="digest"):
        ObjectReference(resource, "x", "md5:bad")


def test_cache_entry_and_wire_helpers() -> None:
    entry = CacheEntry(
        "key",
        {"answer": 42},
        "json-v1",
        FINGERPRINT,
        NOW,
        NOW + timedelta(minutes=5),
        source_version=3,
    )
    assert entry.to_dict()["value"] == {"answer": 42}
    assert normalize_timestamp(NOW) == NOW_TEXT
    assert normalize_timestamp(NOW_TEXT) == NOW_TEXT
    assert validate_base64url("aGVsbG8=") == "aGVsbG8"
    with pytest.raises(ValueError, match="expiry"):
        CacheEntry("key", 1, "json", FINGERPRINT, NOW, NOW)
    with pytest.raises(ValueError, match="key"):
        CacheEntry(None, 1, "json", FINGERPRINT, NOW)
    with pytest.raises(ValueError, match="timezone"):
        normalize_timestamp(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timestamp"):
        normalize_timestamp("2026-01-01")
    with pytest.raises((TypeError, ValueError), match="base64url"):
        validate_base64url("***")


def test_semantics_error_payload_preserves_stable_details() -> None:
    error = InvalidDefinition(
        "invalid",
        requirement="schema.test",
        logical_references=("b", "a", "a"),
        resource_ref="structured:example.customer",
    )
    payload = error.to_dict()
    assert payload["code"] == "MERIDIAN_SEMANTICS_INVALID_DEFINITION"
    assert payload["requirement"] == "schema.test"
    assert payload["logicalReferences"] == ["a", "b"]
    activation = ActivationFailed("retry")
    assert activation.to_dict()["retryable"] is True
    with pytest.raises(TypeError, match="requirement"):
        InvalidDefinition("bad", requirement=1)
