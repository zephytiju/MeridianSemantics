# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from meridian_storage import Expression
from meridian_storage.semantics import (
    CacheCatalogProvider,
    CacheEntry,
    CatalogName,
    DefaultExpression,
    DocumentProfile,
    FieldDefinition,
    FullTextProfile,
    IndexDefinition,
    InvalidDefinition,
    InvalidRelationProfile,
    LocalizedText,
    LogicalKind,
    LogicalType,
    ObjectMetadata,
    ObjectReference,
    Record,
    RecordReference,
    RecordValidationFailed,
    RelationalProfile,
    ResourceReference,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
    StructuredCatalogProvider,
    validate_record,
    validate_schema,
)
from meridian_storage.semantics.canonical import normalize_unicode_name
from meridian_storage.semantics.i18n import normalize_locale, validate_icu_message
from tests.support import DIGEST, NOW, field, schema


@pytest.mark.parametrize(
    "pattern",
    [
        "{1}",
        "{x,}",
        "{x, plural}",
        "{x, select, offset:1 other {x}}",
        "{x, plural, offset:wat other {x}}",
        "{x, plural, offset:-1 other {x}}",
        "{x, select, ? {x} other {y}}",
        "{x, select, male text other {y}}",
        "{x, select, male {x} male {y} other {z}}",
        "{x, select,",
    ],
)
def test_additional_icu_syntax_rejections(pattern: str) -> None:
    with pytest.raises(ValueError):
        validate_icu_message(pattern)


def test_icu_simple_formats_and_plural_categories() -> None:
    text = LocalizedText("en", {"en": "{name}: {n, number}; {day, date, short}"})
    assert text.format("en", {"name": "Ada", "n": 3, "day": "today"}) == ("Ada: 3; today")
    ordinal = LocalizedText(
        "en",
        {"en": "{n, selectordinal, one {#st} two {#nd} few {#rd} other {#th}}"},
    )
    assert [ordinal.format("en", {"n": item}) for item in (1, 2, 3, 11)] == [
        "1st",
        "2nd",
        "3rd",
        "11th",
    ]
    russian = LocalizedText(
        "ru",
        {"ru": "{n, plural, one {one} few {few} many {many} other {other}}"},
    )
    assert [russian.format("ru", {"n": item}) for item in (1, 3, 5, 1.5)] == [
        "one",
        "few",
        "many",
        "other",
    ]
    exact = LocalizedText("en", {"en": "{n, plural, =2 {two} other {#}}"})
    assert exact.format("en", {"n": 2}) == "two"


def test_locale_and_localized_mapping_edges() -> None:
    assert normalize_locale("sl-rozaj-biske") == "sl-rozaj-biske"
    with pytest.raises(ValueError, match="BCP 47"):
        normalize_locale("not_a_locale")
    with pytest.raises(TypeError, match="messages"):
        LocalizedText.from_mapping({"defaultLocale": "en", "messages": "bad"})
    assert LocalizedText.from_mapping({"en": "Hello", "fr": "Bonjour"}).resolve("fr") == ("Bonjour")


def test_schema_parser_and_constructor_edges() -> None:
    with pytest.raises(TypeError, match="logicalType"):
        LogicalType.parse(3)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown logicalType"):
        LogicalType.parse({"kind": "string", "vendor": True})
    with pytest.raises(ValueError, match="enum logical"):
        LogicalType(LogicalKind.ENUM, precision=2, enum_values=("x",))
    with pytest.raises(ValueError, match="decimal logical"):
        LogicalType(LogicalKind.DECIMAL, precision=2, scale=1, enum_values=("x",))
    with pytest.raises(ValueError, match="requires kind"):
        DefaultExpression.from_mapping({"kind": "literal"})
    with pytest.raises(ValueError, match="operand"):
        DefaultExpression("lower", {"kind": "literal", "arguments": 1}).evaluate({})
    with pytest.raises(ValueError, match="booleans"):
        FieldDefinition("x", LogicalType(LogicalKind.STRING), nullable=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique"):
        FieldDefinition(
            "x",
            LogicalType(LogicalKind.STRING),
            sensitivity_labels=("secret", "secret"),
        )
    with pytest.raises(TypeError, match="objects"):
        FieldDefinition.from_mapping({"name": "x", "logicalType": "string", "constraints": []})
    with pytest.raises(ValueError, match="unknown or missing"):
        IndexDefinition.from_mapping({"name": "x", "kind": "hash"})
    with pytest.raises(TypeError, match="array"):
        IndexDefinition.from_mapping({"name": "x", "kind": "hash", "fields": "x"})
    with pytest.raises(TypeError, match="object"):
        IndexDefinition.from_mapping({"name": "x", "kind": "hash", "fields": ["x"], "options": []})


def test_schema_document_and_definition_shape_edges() -> None:
    reference = SchemaReference(CatalogName.STRUCTURED, "example", "edge", "1.0.0")
    with pytest.raises(ValueError, match="identity fields"):
        SchemaDocument(reference, SemanticKind.RELATIONAL, (field("id"),), ("id", "id"))
    index = IndexDefinition("same", "hash", ("id",))
    with pytest.raises(ValueError, match="index names"):
        SchemaDocument(
            reference,
            SemanticKind.RELATIONAL,
            (field("id"),),
            ("id",),
            indexes=(index, index),
        )
    with pytest.raises(ValueError, match="formatVersion"):
        SchemaDocument(
            reference,
            SemanticKind.RELATIONAL,
            (field("id"),),
            ("id",),
            format_version="meridian.schema.v2",
        )
    base = {"semanticKind": "relational", "fields": [{"name": "id", "logicalType": "string"}]}
    cases = [
        ({**base, "formatVersion": "v2"}, "formatVersion"),
        ({**base, "version": "2.0.0"}, "version does not match"),
        ({"semanticKind": "relational"}, "requires semanticKind and fields"),
        ({**base, "identity": "id"}, "identity must be an array"),
        ({**base, "auditPolicy": []}, "auditPolicy must be an object"),
        ({**base, "extensions": []}, "extensions must be an object"),
        ({**base, "fields": "id"}, "fields must be an object or array"),
        ({**base, "indexes": {}}, "indexes must be an array"),
        ({**base, "constraints": ["bad"]}, "entries must be objects"),
    ]
    for definition, match in cases:
        with pytest.raises((TypeError, ValueError), match=match):
            SchemaDocument.from_definition(
                catalog="structured",
                namespace="example",
                name="edge",
                version="1.0.0",
                definition=definition,
            )


def test_schema_structure_validation_edges() -> None:
    with pytest.raises(InvalidDefinition, match="not defined"):
        validate_schema(schema(identity=("missing",)))
    with pytest.raises(InvalidDefinition, match="required scalar"):
        validate_schema(schema(fields=(field("id", nullable=True),)))
    with pytest.raises(InvalidDefinition, match="cannot be empty"):
        validate_schema(schema(identity=()))
    with pytest.raises(InvalidDefinition, match="unknown fields"):
        validate_schema(schema(indexes=(IndexDefinition("bad", "hash", ("missing",)),)))
    with pytest.raises(InvalidDefinition, match="geospatial"):
        validate_schema(schema(indexes=(IndexDefinition("bad", "geospatial", ("id",)),)))
    with pytest.raises(InvalidDefinition, match="relation endpoint"):
        validate_schema(schema(indexes=(IndexDefinition("bad", "relation-endpoint", ("id",)),)))
    mismatched = schema(
        semantic_kind=SemanticKind.DOCUMENT,
        fields=(field("id"), field("body", LogicalKind.JSON)),
        profile={"kind": "geospatial", "pointFields": ["body"]},
    )
    with pytest.raises(InvalidDefinition, match="does not match"):
        validate_schema(mismatched)


@pytest.mark.parametrize(
    "constraints,match",
    [
        ({"minLength": True}, "non-negative"),
        ({"minLength": 3, "maxLength": 2}, "exceeds"),
        ({"min": "not-number"}, "not numeric"),
        ({"min": "Infinity"}, "finite"),
        ({"uniqueItems": "yes"}, "boolean"),
        ({"pattern": "["}, "invalid"),
    ],
)
def test_constraint_definition_edges(constraints: dict[str, object], match: str) -> None:
    with pytest.raises(InvalidDefinition, match=match):
        validate_schema(schema(fields=(field("id", constraints=constraints),)))


def test_constraint_applicability_is_portable() -> None:
    with pytest.raises(InvalidDefinition, match="string constraints"):
        validate_schema(
            schema(
                fields=(
                    field("id"),
                    field("value", LogicalKind.INT32, constraints={"minLength": 1}),
                )
            )
        )
    with pytest.raises(InvalidDefinition, match="item constraints"):
        validate_schema(schema(fields=(field("id", constraints={"minItems": 1}),)))
    with pytest.raises(InvalidDefinition, match="numeric constraints"):
        validate_schema(schema(fields=(field("id", constraints={"min": 1}),)))


def test_reference_and_media_constraint_definition_edges() -> None:
    with pytest.raises(InvalidDefinition, match="non-empty"):
        validate_schema(
            schema(
                fields=(
                    field("id"),
                    field(
                        "reference",
                        LogicalKind.RECORD_REF,
                        constraints={"allowedCollections": ()},
                    ),
                )
            )
        )
    with pytest.raises(InvalidDefinition, match="invalid allowedCollections"):
        validate_schema(
            schema(
                fields=(
                    field("id"),
                    field(
                        "reference",
                        LogicalKind.RECORD_REF,
                        constraints={"allowedCollections": ({"bad": 1},)},
                    ),
                )
            )
        )
    with pytest.raises(InvalidDefinition, match="requires ObjectRef"):
        validate_schema(
            schema(
                fields=(
                    field("id"),
                    field("value", constraints={"mediaTypes": ("text/plain",)}),
                )
            )
        )
    with pytest.raises(InvalidDefinition, match="invalid mediaTypes"):
        validate_schema(
            schema(
                fields=(
                    field("id"),
                    field(
                        "value",
                        LogicalKind.OBJECT_REF,
                        constraints={"mediaTypes": ("bad",)},
                    ),
                )
            )
        )


@pytest.mark.parametrize(
    "logical_type,value",
    [
        (LogicalType(LogicalKind.STRING), 1),
        (LogicalType(LogicalKind.DECIMAL, precision=4, scale=2), "bad"),
        (LogicalType(LogicalKind.DECIMAL, precision=4, scale=2), "NaN"),
        (LogicalType(LogicalKind.UUID), "123E4567-E89B-12D3-A456-426614174000"),
        (LogicalType(LogicalKind.UTC_TIMESTAMP), 1),
        (LogicalType(LogicalKind.JSON), float("nan")),
        (LogicalType(LogicalKind.OBJECT_REF), {"resourceRef": "bad", "objectId": "x"}),
        (LogicalType(LogicalKind.WGS84_POINT), [1, 2]),
    ],
)
def test_additional_record_type_errors(logical_type: LogicalType, value: object) -> None:
    with pytest.raises(RecordValidationFailed):
        validate_record(
            schema(fields=(field("id"), field("value", logical_type=logical_type))),
            {"id": "x", "value": value},
        )


@pytest.mark.parametrize(
    "key,limit,value",
    [("min", 4, 3), ("max", 2, 3), ("exclusiveMin", 3, 3), ("exclusiveMax", 3, 3)],
)
def test_numeric_constraint_failures(key: str, limit: int, value: int) -> None:
    with pytest.raises(RecordValidationFailed, match="constraint failed"):
        validate_record(
            schema(
                fields=(
                    field("id"),
                    field("value", LogicalKind.INT32, constraints={key: limit}),
                )
            ),
            {"id": "x", "value": value},
        )


def test_profile_validation_failure_edges() -> None:
    cases = [
        schema(
            profile=RelationalProfile(alternate_keys=(("missing",),)).to_dict(),
        ),
        schema(
            semantic_kind=SemanticKind.DOCUMENT,
            profile=DocumentProfile("id").to_dict(),
        ),
        schema(
            semantic_kind=SemanticKind.SEARCH,
            profile=FullTextProfile(("missing",)).to_dict(),
        ),
    ]
    for document in cases:
        with pytest.raises(InvalidDefinition):
            validate_schema(document)
    artifact = schema(
        catalog=CatalogName.OBJECT,
        semantic_kind=SemanticKind.ARTIFACT,
        fields=(field("digest"),),
        identity=(),
        profile={"kind": "artifact", "profile": "artifact"},
    )
    with pytest.raises(InvalidDefinition, match="lineage"):
        validate_schema(artifact)


def test_relation_profile_field_and_index_failures() -> None:
    customers = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    orders = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    profile = {
        "kind": "relation",
        "sourceField": "source",
        "targetField": "target",
        "directed": True,
        "sourceCollections": [customers.to_dict()],
        "targetCollections": [orders.to_dict()],
    }
    bad_fields = schema(
        semantic_kind=SemanticKind.RELATION,
        fields=(field("id"), field("source"), field("target")),
        profile=profile,
    )
    with pytest.raises(InvalidRelationProfile, match="incompatible"):
        validate_schema(bad_fields)
    no_indexes = schema(
        semantic_kind=SemanticKind.RELATION,
        fields=(
            field("id"),
            field("source", LogicalKind.RECORD_REF),
            field("target", LogicalKind.RECORD_REF),
        ),
        profile=profile,
    )
    with pytest.raises(InvalidRelationProfile, match="indexes"):
        validate_schema(no_indexes)


def test_resource_model_edge_failures() -> None:
    with pytest.raises(TypeError, match="namespace"):
        ResourceReference.parse({"catalog": "structured", "namespace": 1, "name": "x"})
    with pytest.raises(TypeError, match="version"):
        SchemaReference.parse(
            {"catalog": "structured", "namespace": "example", "name": "x", "version": 1}
        )
    with pytest.raises(TypeError, match="unsupported Schema"):
        SchemaReference.parse("structured:example.x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires"):
        RecordReference.from_mapping({"collectionRef": {}, "recordId": 1, "extra": True})
    with pytest.raises(TypeError, match="object"):
        RecordReference.from_mapping({"collectionRef": "bad", "recordId": 1})
    resource = ResourceReference(CatalogName.STRUCTURED, "example", "records")
    with pytest.raises(ValueError, match="format"):
        Record(resource, 1, {}, 1, NOW, NOW, format_version="v2")
    object_resource = ResourceReference(CatalogName.OBJECT, "example", "objects")
    object_ref = ObjectReference(object_resource, "x")
    assert "digest" not in object_ref.to_dict()
    with pytest.raises(TypeError, match="integer"):
        ObjectMetadata(object_ref, DIGEST, True, "text/plain", NOW)
    with pytest.raises(ValueError, match="mutability"):
        ObjectMetadata(object_ref, DIGEST, 1, "text/plain", NOW, mutability="sometimes")
    with pytest.raises(ValueError, match="format"):
        ObjectMetadata(object_ref, DIGEST, 1, "text/plain", NOW, format_version="v2")
    with pytest.raises(ValueError, match="schemaFingerprint"):
        CacheEntry("x", 1, "json", "bad", NOW)
    with pytest.raises(TypeError, match="base64url"):
        from meridian_storage.semantics.resources import validate_base64url

        validate_base64url(1)
    with pytest.raises(ValueError, match="valid UTC"):
        from meridian_storage.semantics.resources import normalize_timestamp

        normalize_timestamp("2026-02-30T00:00:00.000000Z")


def test_catalog_normalization_edge_branches() -> None:
    structured = StructuredCatalogProvider()
    cache = CacheCatalogProvider()
    assert structured.create_surface().catalog_name == "structured"
    assert cache.create_surface().catalog_name == "cache"
    assert cache.manifest().catalog_name == "cache"
    surface = structured.create_surface()
    assert structured.normalize(
        surface.put(resource="example.x", data={}, expected_version=0)
    ).idempotent
    assert structured.normalize(
        surface.delete(resource="example.x", where={}, expected_version=0)
    ).idempotent
    with pytest.raises(InvalidDefinition, match="unknown or missing"):
        structured.normalize(Expression("structured", "publish_schema", {}))
    with pytest.raises(InvalidDefinition, match="array"):
        structured.normalize(
            Expression(
                "structured",
                "traverse",
                {
                    "resource": "example.x",
                    "start": {},
                    "relationCollections": "bad",
                    "allNeighbors": False,
                    "maxDepth": 1,
                },
            )
        )


def test_miscellaneous_normalization_guards() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        normalize_unicode_name("", "name")
    with pytest.raises(ValueError):
        normalize_unicode_name(1, "name")
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        from meridian_storage.semantics.resources import normalize_timestamp

        normalize_timestamp(naive)
