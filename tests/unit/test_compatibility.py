# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import replace

import pytest

from meridian_storage.semantics import (
    CompatibilityClass,
    DefaultExpression,
    IndexDefinition,
    LogicalKind,
    LogicalType,
    SemanticKind,
    classify_compatibility,
)
from tests.support import field, schema


def test_identical_shape_across_versions_is_noop() -> None:
    source = schema("1.0.0")
    target = schema("1.0.1")
    report = classify_compatibility(source, target)
    assert report.classification is CompatibilityClass.IDENTICAL
    assert report.activation_requirement == "no-op"
    assert report.changes == ()
    assert not report.breaking
    assert report.to_dict()["sourceFingerprint"] == source.fingerprint


@pytest.mark.parametrize(
    "target,classification,path",
    [
        (
            schema(
                "1.1.0",
                fields=(field("id"), field("name", nullable=True), field("note", nullable=True)),
            ),
            CompatibilityClass.BACKWARD,
            "fields.note",
        ),
        (
            schema("1.1.0", fields=(field("id"), field("name", nullable=True), field("note"))),
            CompatibilityClass.BREAKING,
            "fields.note",
        ),
        (
            schema("1.1.0", fields=(field("id"),)),
            CompatibilityClass.BREAKING,
            "fields.name",
        ),
        (
            schema(
                "1.1.0",
                fields=(
                    field("id"),
                    field("name", nullable=True),
                    field(
                        "count",
                        default=DefaultExpression("literal", 0),
                        logical_type=LogicalType(LogicalKind.INT32),
                    ),
                ),
            ),
            CompatibilityClass.BACKWARD,
            "fields.count",
        ),
    ],
)
def test_field_addition_and_removal_classification(
    target: object,
    classification: CompatibilityClass,
    path: str,
) -> None:
    report = classify_compatibility(schema("1.0.0"), target)  # type: ignore[arg-type]
    assert report.classification is classification
    assert path in {change.path for change in report.changes}


def test_numeric_enum_nullability_and_default_changes() -> None:
    source = schema(
        fields=(
            field("id"),
            field("count", logical_type=LogicalType(LogicalKind.INT16)),
            field("state", logical_type=LogicalType(LogicalKind.ENUM, enum_values=("new",))),
            field("description"),
        )
    )
    widened = schema(
        "1.1.0",
        fields=(
            field("id"),
            field("count", logical_type=LogicalType(LogicalKind.INT32)),
            field(
                "state",
                logical_type=LogicalType(LogicalKind.ENUM, enum_values=("new", "active")),
            ),
            field("description", nullable=True),
        ),
    )
    report = classify_compatibility(source, widened)
    assert report.classification is CompatibilityClass.CONDITIONAL
    classes = {change.path: change.classification for change in report.changes}
    assert classes["fields.count.logicalType"] is CompatibilityClass.CONDITIONAL
    assert classes["fields.state.logicalType"] is CompatibilityClass.BACKWARD
    assert classes["fields.description.nullable"] is CompatibilityClass.BACKWARD
    changed_default = replace(
        widened,
        ref=replace(widened.ref, version="1.2.0"),
        fields=tuple(
            replace(item, default_expression=DefaultExpression("literal", "unknown"))
            if item.name == "description"
            else item
            for item in widened.fields
        ),
    )
    assert (
        classify_compatibility(widened, changed_default).classification
        is CompatibilityClass.CONDITIONAL
    )


def test_index_and_metadata_evolution() -> None:
    source = schema()
    with_index = schema(
        "1.1.0",
        indexes=(IndexDefinition("by_name", "btree", ("name",)),),
    )
    report = classify_compatibility(source, with_index)
    assert report.classification is CompatibilityClass.DATA_COMPATIBLE
    assert report.activation_requirement == "Adapter migration"
    removed = classify_compatibility(with_index, schema("1.2.0"))
    assert removed.classification is CompatibilityClass.CONDITIONAL
    metadata = replace(
        source,
        ref=replace(source.ref, version="1.0.1"),
        retention_label="archive",
    )
    assert classify_compatibility(source, metadata).classification is CompatibilityClass.METADATA


def test_mutability_is_behavioral_compatibility() -> None:
    source = schema(fields=(field("id"), field("value")))
    immutable = schema("2.0.0", fields=(field("id"), field("value", mutable=False)))
    assert classify_compatibility(source, immutable).classification is CompatibilityClass.BREAKING
    mutable = schema("2.1.0", fields=(field("id"), field("value")))
    assert classify_compatibility(immutable, mutable).classification is CompatibilityClass.BACKWARD


def test_semantic_and_relation_changes_are_breaking() -> None:
    customers = {"catalog": "structured", "namespace": "example", "name": "customers"}
    orders = {"catalog": "structured", "namespace": "example", "name": "orders"}
    profile = {
        "kind": "relation",
        "sourceField": "source",
        "targetField": "target",
        "directed": True,
        "sourceCollections": [customers],
        "targetCollections": [orders],
    }
    relation_fields = (
        field("id"),
        field("source", LogicalKind.RECORD_REF),
        field("target", LogicalKind.RECORD_REF),
    )
    indexes = (
        IndexDefinition("source_idx", "relation-endpoint", ("source",)),
        IndexDefinition("target_idx", "relation-endpoint", ("target",)),
    )
    source = schema(
        name="edges",
        semantic_kind=SemanticKind.RELATION,
        fields=relation_fields,
        indexes=indexes,
        profile=profile,
    )
    target = schema(
        "2.0.0",
        name="edges",
        semantic_kind=SemanticKind.RELATION,
        fields=relation_fields,
        indexes=indexes,
        profile={**profile, "directed": False},
    )
    assert classify_compatibility(source, target).breaking
    with pytest.raises(ValueError, match="stable Schema id"):
        classify_compatibility(source, schema(name="different"))
