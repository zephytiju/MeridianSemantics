# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Mapping

import pytest

from meridian_storage.semantics import (
    FIELD_REMOVAL,
    PROFILE_EXTENSION_KEY,
    Cardinality,
    CatalogName,
    DefaultExpression,
    FieldDefinition,
    IndexDefinition,
    InvalidDefinition,
    InvalidRelationProfile,
    LogicalKind,
    LogicalType,
    RecordReference,
    RecordValidationFailed,
    ReferenceViolation,
    ResourceReference,
    SemanticKind,
    UnsupportedSemantic,
    validate_patch,
    validate_record,
    validate_schema,
)
from tests.support import DIGEST, NOW_TEXT, field, schema


@pytest.mark.parametrize(
    "logical_type,value,normalized",
    [
        (LogicalType(LogicalKind.BOOLEAN), True, True),
        (LogicalType(LogicalKind.INT8), 127, 127),
        (LogicalType(LogicalKind.INT16), -32768, -32768),
        (LogicalType(LogicalKind.INT32), 42, 42),
        (LogicalType(LogicalKind.INT64), 2**40, 2**40),
        (LogicalType(LogicalKind.DECIMAL, precision=6, scale=2), "12.30", "12.30"),
        (LogicalType(LogicalKind.FLOAT64), 2, 2.0),
        (LogicalType(LogicalKind.STRING), "text", "text"),
        (LogicalType(LogicalKind.BYTES), "aGVsbG8=", "aGVsbG8"),
        (
            LogicalType(LogicalKind.UUID),
            "123e4567-e89b-12d3-a456-426614174000",
            "123e4567-e89b-12d3-a456-426614174000",
        ),
        (LogicalType(LogicalKind.UTC_TIMESTAMP), NOW_TEXT, NOW_TEXT),
        (LogicalType(LogicalKind.DATE), "2026-08-25", "2026-08-25"),
        (LogicalType(LogicalKind.DURATION), "P1DT2H3M4.5S", "P1DT2H3M4.5S"),
        (LogicalType(LogicalKind.ENUM, enum_values=("open", "closed")), "open", "open"),
        (LogicalType(LogicalKind.JSON), {"nested": [1, True]}, {"nested": (1, True)}),
        (
            LogicalType(LogicalKind.RECORD_REF),
            {
                "collectionRef": {
                    "catalog": "structured",
                    "namespace": "example",
                    "name": "customers",
                },
                "recordId": 42,
            },
            {
                "collectionRef": {
                    "catalog": "structured",
                    "namespace": "example",
                    "name": "customers",
                },
                "recordId": 42,
            },
        ),
        (
            LogicalType(LogicalKind.OBJECT_REF),
            {
                "resourceRef": {
                    "catalog": "object",
                    "namespace": "example",
                    "name": "objects",
                },
                "objectId": "file.bin",
                "digest": DIGEST,
            },
            {
                "resourceRef": {
                    "catalog": "object",
                    "namespace": "example",
                    "name": "objects",
                },
                "objectId": "file.bin",
                "digest": DIGEST,
            },
        ),
        (
            LogicalType(LogicalKind.WGS84_POINT),
            {"longitude": -122.4, "latitude": 37.8},
            {"latitude": 37.8, "longitude": -122.4},
        ),
    ],
)
def test_all_portable_logical_values(
    logical_type: LogicalType,
    value: object,
    normalized: object,
) -> None:
    document = schema(fields=(field("id"), field("value", logical_type=logical_type)))
    result = validate_record(document, {"id": "x", "value": value})
    assert result["value"] == normalized


@pytest.mark.parametrize(
    "logical_type,value",
    [
        (LogicalType(LogicalKind.BOOLEAN), 1),
        (LogicalType(LogicalKind.INT8), 128),
        (LogicalType(LogicalKind.DECIMAL, precision=4, scale=2), "123.456"),
        (LogicalType(LogicalKind.FLOAT64), float("inf")),
        (LogicalType(LogicalKind.BYTES), "***"),
        (LogicalType(LogicalKind.UUID), "not-a-uuid"),
        (LogicalType(LogicalKind.DATE), "2026-02-30"),
        (LogicalType(LogicalKind.DURATION), "one day"),
        (LogicalType(LogicalKind.ENUM, enum_values=("x",)), "y"),
        (LogicalType(LogicalKind.WGS84_POINT), {"longitude": 200, "latitude": 0}),
        (LogicalType(LogicalKind.RECORD_REF), {"bad": True}),
        (LogicalType(LogicalKind.OBJECT_REF), {"objectId": "x"}),
    ],
)
def test_invalid_portable_logical_values(logical_type: LogicalType, value: object) -> None:
    document = schema(fields=(field("id"), field("value", logical_type=logical_type)))
    with pytest.raises(RecordValidationFailed):
        validate_record(document, {"id": "x", "value": value})


def test_cardinality_constraints_defaults_and_patch() -> None:
    document = schema(
        fields=(
            field("id", mutable=False),
            FieldDefinition(
                "tags",
                LogicalType(LogicalKind.STRING),
                Cardinality.MANY,
                constraints={"minItems": 1, "maxItems": 3, "uniqueItems": True},
            ),
            field(
                "slug",
                constraints={"minLength": 2, "maxLength": 12, "pattern": "^[a-z]+$"},
            ),
            field("optional", nullable=True),
            field("copy", default=DefaultExpression("field", "slug")),
        )
    )
    result = validate_record(document, {"id": "1", "tags": ["a", "b"], "slug": "hello"})
    assert result["copy"] == "hello"
    patch = validate_patch(
        document,
        result,
        {"slug": "world", "optional": FIELD_REMOVAL},
    )
    assert patch == {"slug": "world"}
    with pytest.raises(RecordValidationFailed, match="immutable"):
        validate_patch(document, result, {"id": "2"})
    with pytest.raises(RecordValidationFailed, match="unique"):
        validate_record(document, {"id": "1", "tags": ["a", "a"], "slug": "hello"})
    with pytest.raises(RecordValidationFailed, match="pattern"):
        validate_record(document, {"id": "1", "tags": ["a"], "slug": "INVALID"})


def test_record_unknown_missing_null_and_cyclic_defaults() -> None:
    document = schema(
        fields=(
            field("id"),
            field("a", default=DefaultExpression("field", "b")),
            field("b", default=DefaultExpression("field", "a")),
        )
    )
    with pytest.raises(RecordValidationFailed, match="cyclic"):
        validate_record(document, {"id": "x"})
    with pytest.raises(RecordValidationFailed, match="unknown"):
        validate_record(schema(), {"id": "x", "extra": 1})
    with pytest.raises(RecordValidationFailed, match="null"):
        validate_record(schema(), {"id": None})
    with pytest.raises(RecordValidationFailed, match="missing"):
        validate_record(schema(fields=(field("id"), field("required"))), {"id": "x"})


class _Resolver:
    def __init__(self, existing: bool) -> None:
        self.existing = existing

    def exists(self, reference: RecordReference) -> bool:
        return self.existing and reference.record_id == 42


def test_record_reference_allowed_endpoint_and_existence() -> None:
    customers = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    orders = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    reference_field = field(
        "customer",
        LogicalKind.RECORD_REF,
        constraints={"allowedCollections": (customers.to_dict(),)},
    )
    document = schema(fields=(field("id"), reference_field))
    allowed = {"collectionRef": customers.to_dict(), "recordId": 42}
    disallowed = {"collectionRef": orders.to_dict(), "recordId": 42}
    assert validate_record(
        document, {"id": "o1", "customer": allowed}, reference_resolver=_Resolver(True)
    )
    with pytest.raises(ReferenceViolation, match="disallowed"):
        validate_record(document, {"id": "o1", "customer": disallowed})
    with pytest.raises(ReferenceViolation, match="does not exist"):
        validate_record(
            document, {"id": "o1", "customer": allowed}, reference_resolver=_Resolver(False)
        )


@pytest.mark.parametrize(
    "document",
    [
        schema(
            semantic_kind=SemanticKind.DOCUMENT,
            fields=(field("id"), field("body", LogicalKind.JSON)),
            profile={"kind": "document", "bodyField": "body"},
        ),
        schema(
            semantic_kind=SemanticKind.KEY_VALUE,
            fields=(
                field("key"),
                field("value", LogicalKind.JSON),
                field("expires", LogicalKind.UTC_TIMESTAMP),
            ),
            identity=("key",),
            profile={
                "kind": "key-value",
                "keyField": "key",
                "valueField": "value",
                "expiresAtField": "expires",
            },
        ),
        schema(
            semantic_kind=SemanticKind.SEARCH,
            fields=(field("id"), field("body"), field("normalized")),
            indexes=(IndexDefinition("text", "full-text", ("body",)),),
            profile={"kind": "search", "sourceFields": ["body"], "normalizedField": "normalized"},
        ),
        schema(
            semantic_kind=SemanticKind.GEOSPATIAL,
            fields=(field("id"), field("location", LogicalKind.WGS84_POINT)),
            indexes=(IndexDefinition("geo", "geospatial", ("location",)),),
            profile={"kind": "geospatial", "pointFields": ["location"]},
        ),
        schema(
            semantic_kind=SemanticKind.TIME_SERIES,
            fields=(
                field("id"),
                field("observed", LogicalKind.UTC_TIMESTAMP),
                field("device"),
                field("region"),
                field("value", LogicalKind.FLOAT64),
            ),
            profile={
                "kind": "time-series",
                "timestampField": "observed",
                "seriesIdentity": ["device"],
                "dimensions": ["region"],
                "measurements": ["value"],
            },
        ),
        schema(
            catalog=CatalogName.OBJECT,
            semantic_kind=SemanticKind.ARTIFACT,
            fields=(field("digest"),),
            identity=(),
            profile={"kind": "artifact", "profile": "artifact"},
            lineage_policy={"required": True},
        ),
        schema(
            catalog=CatalogName.CACHE,
            semantic_kind=SemanticKind.CACHE,
            fields=(field("key"), field("value", LogicalKind.JSON)),
            identity=("key",),
            profile={"kind": "cache"},
        ),
    ],
)
def test_valid_specialized_profiles(document: object) -> None:
    assert validate_schema(document) is document  # type: ignore[arg-type]


def test_relation_profile_requires_fields_indexes_and_matching_endpoints() -> None:
    sources = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    targets = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    relation = schema(
        name="customer_orders",
        semantic_kind=SemanticKind.RELATION,
        fields=(
            field("id"),
            field(
                "source",
                LogicalKind.RECORD_REF,
                constraints={"allowedCollections": (sources.to_dict(),)},
            ),
            field(
                "target",
                LogicalKind.RECORD_REF,
                constraints={"allowedCollections": (targets.to_dict(),)},
            ),
        ),
        indexes=(
            IndexDefinition("by_source", "relation-endpoint", ("source",)),
            IndexDefinition("by_target", "relation-endpoint", ("target",)),
        ),
        profile={
            "kind": "relation",
            "sourceField": "source",
            "targetField": "target",
            "directed": False,
            "sourceCollections": [sources.to_dict()],
            "targetCollections": [targets.to_dict()],
        },
    )
    assert validate_schema(relation) is relation
    wrong = schema(
        name="customer_orders",
        semantic_kind=SemanticKind.RELATION,
        fields=relation.fields,
        indexes=relation.indexes,
        profile={
            **relation.profile.to_dict(),  # type: ignore[union-attr]
            "targetCollections": [sources.to_dict()],
        },
    )
    with pytest.raises(InvalidRelationProfile, match="disagrees"):
        validate_schema(wrong)


def test_schema_validation_rejects_catalog_profile_index_and_constraint_mismatches() -> None:
    with pytest.raises(InvalidDefinition, match="belongs"):
        validate_schema(schema(catalog=CatalogName.OBJECT))
    with pytest.raises(InvalidDefinition, match="requires profile"):
        validate_schema(schema(semantic_kind=SemanticKind.SEARCH))
    with pytest.raises(InvalidDefinition, match="full-text"):
        validate_schema(
            schema(
                indexes=(IndexDefinition("bad", "full-text", ("id",)),),
                fields=(field("id", LogicalKind.INT64),),
            )
        )
    with pytest.raises(UnsupportedSemantic, match="unsupported"):
        validate_schema(schema(fields=(field("id", constraints={"vendorLimit": 1}),)))
    with pytest.raises(UnsupportedSemantic, match="non-portable"):
        validate_schema(schema(fields=(field("id", constraints={"pattern": "(?=x)"}),)))
    with pytest.raises(InvalidDefinition, match="non-negative"):
        validate_schema(schema(fields=(field("id", constraints={"minLength": -1}),)))
    with pytest.raises(InvalidDefinition, match="requires RecordRef"):
        validate_schema(schema(fields=(field("id", constraints={"allowedCollections": ({},)}),)))


def test_field_removal_is_not_a_regular_value() -> None:
    with pytest.raises(RecordValidationFailed, match="only in patch"):
        validate_record(schema(), {"id": FIELD_REMOVAL})
    document = schema(fields=(field("id"), field("required")))
    with pytest.raises(RecordValidationFailed, match="cannot be removed"):
        validate_patch(document, {"id": "x", "required": "v"}, {"required": FIELD_REMOVAL})


def test_validate_record_returns_immutable_mapping() -> None:
    result = validate_record(schema(), {"id": "x", "name": None})
    assert isinstance(result, Mapping)
    with pytest.raises(TypeError):
        result["id"] = "y"  # type: ignore[index]


def test_unicode_normalized_patch_names() -> None:
    document = schema(fields=(field("id"), field("café", nullable=True)))
    patch = validate_patch(document, {"id": "x", "café": "old"}, {"cafe\u0301": "new"})
    assert patch["café"] == "new"


def test_profile_extension_constant_is_namespaced() -> None:
    assert PROFILE_EXTENSION_KEY == "org.meridian.profile/v1"
