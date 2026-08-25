# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from decimal import Decimal

import pytest

from meridian_storage.semantics import (
    PROFILE_EXTENSION_KEY,
    Cardinality,
    CatalogName,
    DefaultExpression,
    DocumentProfile,
    FieldDefinition,
    IndexDefinition,
    LocalizedText,
    LogicalKind,
    LogicalType,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
)
from meridian_storage.semantics.schemas import decimal_precision_scale
from tests.support import field, schema


@pytest.mark.parametrize(
    "kind",
    [
        LogicalKind.BOOLEAN,
        LogicalKind.INT8,
        LogicalKind.INT16,
        LogicalKind.INT32,
        LogicalKind.INT64,
        LogicalKind.FLOAT64,
        LogicalKind.STRING,
        LogicalKind.BYTES,
        LogicalKind.UUID,
        LogicalKind.UTC_TIMESTAMP,
        LogicalKind.DATE,
        LogicalKind.DURATION,
        LogicalKind.JSON,
        LogicalKind.RECORD_REF,
        LogicalKind.OBJECT_REF,
        LogicalKind.WGS84_POINT,
    ],
)
def test_simple_logical_type_round_trip(kind: LogicalKind) -> None:
    logical = LogicalType(kind)
    assert LogicalType.parse(logical.to_wire()) == logical
    assert logical.scalar is (
        kind.value
        in {
            "boolean",
            "int8",
            "int16",
            "int32",
            "int64",
            "float64",
            "string",
            "bytes",
            "uuid",
            "utcTimestamp",
            "date",
            "duration",
        }
    )


def test_parameterized_logical_types() -> None:
    decimal = LogicalType(LogicalKind.DECIMAL, precision=18, scale=4)
    enum = LogicalType(LogicalKind.ENUM, enum_values=("new", "active"))
    assert LogicalType.parse(decimal.to_wire()) == decimal
    assert LogicalType.parse(enum.to_wire()) == enum
    with pytest.raises(ValueError, match="requires parameters"):
        LogicalType.parse("decimal")
    with pytest.raises(ValueError, match="precision"):
        LogicalType(LogicalKind.DECIMAL, precision=0, scale=0)
    with pytest.raises(ValueError, match="scale"):
        LogicalType(LogicalKind.DECIMAL, precision=4, scale=5)
    with pytest.raises(ValueError, match="unique"):
        LogicalType(LogicalKind.ENUM, enum_values=("x", "x"))
    with pytest.raises(ValueError, match="does not accept"):
        LogicalType(LogicalKind.STRING, precision=4)


def test_default_expressions_are_bounded_and_deterministic() -> None:
    values = {"first": "Ada", "last": "Lovelace", "none": None}
    concat = DefaultExpression(
        "concat",
        (
            {"kind": "field", "arguments": "first"},
            " ",
            {"kind": "upper", "arguments": {"kind": "field", "arguments": "last"}},
        ),
    )
    assert concat.evaluate(values) == "Ada LOVELACE"
    assert DefaultExpression("coalesce", (None, "fallback")).evaluate(values) == "fallback"
    assert DefaultExpression("field", "first").evaluate(values) == "Ada"
    assert DefaultExpression.from_mapping(42).evaluate(values) == 42
    assert DefaultExpression.from_mapping(concat.to_dict()) == concat
    with pytest.raises(KeyError):
        DefaultExpression("field", "missing").evaluate(values)
    with pytest.raises(ValueError, match="unsupported"):
        DefaultExpression("now", ())
    with pytest.raises(ValueError, match="strings"):
        DefaultExpression("concat", (1, 2)).evaluate(values)
    with pytest.raises(ValueError, match="requires"):
        DefaultExpression("field", ("bad",))


def test_field_and_index_mapping_round_trip() -> None:
    definition = FieldDefinition(
        "cafe\u0301",
        LogicalType(LogicalKind.STRING),
        Cardinality.MANY,
        nullable=True,
        constraints={"minItems": 1},
        sensitivity_labels=("pii",),
        documentation=LocalizedText("en", {"en": "Names"}),
        annotations={"org.example/source": "crm"},
    )
    assert definition.name == "café"
    assert FieldDefinition.from_mapping(definition.to_dict()) == definition
    index = IndexDefinition("by_name", "btree", ("café",), unique=True)
    assert IndexDefinition.from_mapping(index.to_dict()) == index
    with pytest.raises(ValueError, match="unknown or missing"):
        FieldDefinition.from_mapping({"name": "id"})
    with pytest.raises(ValueError, match="unsupported"):
        IndexDefinition("idx", "native", ("id",))
    with pytest.raises(ValueError, match="non-empty"):
        IndexDefinition("idx", "hash", ())


def test_schema_mapping_canonicalization_and_core_definition() -> None:
    definition = {
        "semanticKind": "document",
        "fields": {
            "body": {"logicalType": "json"},
            "id": {"logicalType": "string"},
        },
        "identity": ["id"],
        "extensions": {
            PROFILE_EXTENSION_KEY: {
                "kind": "document",
                "bodyField": "body",
                "unknownFields": "closed",
                "indexedPaths": [],
            }
        },
    }
    document = SchemaDocument.from_definition(
        catalog="structured",
        namespace="example",
        name="document",
        version="1.0.0",
        definition=definition,
    )
    assert [item.name for item in document.fields] == ["body", "id"]
    assert isinstance(document.profile, DocumentProfile)
    payload = document.to_dict()
    assert payload["id"] == "structured:example.document"
    assert payload["fingerprint"] == document.fingerprint
    assert (
        SchemaDocument.from_definition(
            catalog=CatalogName.STRUCTURED,
            namespace="example",
            name="document",
            version="1.0.0",
            definition=payload,
        )
        == document
    )
    core = document.to_core_definition()
    assert core.ref == document.ref.to_core()
    assert core.definition["fingerprint"] == document.fingerprint


def test_schema_rejects_address_fingerprint_and_shape_mismatches() -> None:
    base = schema()
    payload = base.to_dict()
    payload["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        SchemaDocument.from_definition(
            catalog="structured",
            namespace="example",
            name="customer",
            version="1.0.0",
            definition=payload,
        )
    with pytest.raises(ValueError, match="id does not match"):
        SchemaDocument.from_definition(
            catalog="structured",
            namespace="example",
            name="customer",
            version="1.0.0",
            definition={"semanticKind": "relational", "fields": [], "id": "structured:x.y"},
        )
    with pytest.raises(ValueError, match="unknown Schema"):
        SchemaDocument.from_definition(
            catalog="structured",
            namespace="example",
            name="customer",
            version="1.0.0",
            definition={"semanticKind": "relational", "fields": [], "extra": True},
        )


def test_schema_constructor_invariants() -> None:
    reference = SchemaReference(CatalogName.STRUCTURED, "example", "customer", "1.0.0")
    with pytest.raises(ValueError, match="cannot be empty"):
        SchemaDocument(reference, SemanticKind.RELATIONAL, (), ())
    with pytest.raises(ValueError, match="unique"):
        SchemaDocument(
            reference,
            SemanticKind.RELATIONAL,
            (field("id"), field("id")),
            ("id",),
        )
    with pytest.raises(ValueError, match="consistency"):
        SchemaDocument(reference, SemanticKind.RELATIONAL, (field("id"),), ("id",), consistency="x")
    with pytest.raises(ValueError, match="profile extension"):
        _ = schema(extensions={PROFILE_EXTENSION_KEY: "bad"}).profile


def test_decimal_precision_scale() -> None:
    assert decimal_precision_scale(Decimal("123.40")) == (5, 2)
    assert decimal_precision_scale(Decimal("0.00")) == (3, 2)
    with pytest.raises(ValueError, match="finite"):
        decimal_precision_scale(Decimal("NaN"))
