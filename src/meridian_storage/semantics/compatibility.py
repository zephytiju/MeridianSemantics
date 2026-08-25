# SPDX-License-Identifier: Apache-2.0
"""Deterministic Schema evolution classification for Meridian V1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .profiles import RelationProfile
from .schemas import FieldDefinition, LogicalKind, SchemaDocument


class CompatibilityClass(StrEnum):
    IDENTICAL = "identical"
    METADATA = "metadata-compatible"
    BACKWARD = "backward-compatible"
    DATA_COMPATIBLE = "data-compatible-physical-change"
    CONDITIONAL = "conditionally-compatible"
    BREAKING = "breaking"


_SEVERITY = {
    CompatibilityClass.IDENTICAL: 0,
    CompatibilityClass.METADATA: 1,
    CompatibilityClass.BACKWARD: 2,
    CompatibilityClass.DATA_COMPATIBLE: 3,
    CompatibilityClass.CONDITIONAL: 4,
    CompatibilityClass.BREAKING: 5,
}


@dataclass(frozen=True, slots=True, order=True)
class SchemaChange:
    path: str
    classification: CompatibilityClass
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "classification": self.classification.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    source_fingerprint: str
    target_fingerprint: str
    classification: CompatibilityClass
    activation_requirement: str
    changes: tuple[SchemaChange, ...]

    @property
    def breaking(self) -> bool:
        return self.classification is CompatibilityClass.BREAKING

    def to_dict(self) -> dict[str, object]:
        return {
            "sourceFingerprint": self.source_fingerprint,
            "targetFingerprint": self.target_fingerprint,
            "classification": self.classification.value,
            "activationRequirement": self.activation_requirement,
            "changes": [item.to_dict() for item in self.changes],
        }


def classify_compatibility(
    source: SchemaDocument,
    target: SchemaDocument,
) -> CompatibilityReport:
    if source.ref.schema_id != target.ref.schema_id:
        raise ValueError("compatibility comparison requires one stable Schema id")
    changes: list[SchemaChange] = []
    if source.semantic_kind is not target.semantic_kind:
        _change(
            changes,
            "semanticKind",
            CompatibilityClass.BREAKING,
            "semantic kind changed",
        )
    if source.identity != target.identity:
        _change(changes, "identity", CompatibilityClass.BREAKING, "identity changed")
    source_fields = source.field_map
    target_fields = target.field_map
    for name in sorted(set(source_fields) - set(target_fields)):
        _change(
            changes,
            f"fields.{name}",
            CompatibilityClass.BREAKING,
            "field was removed or renamed",
        )
    for name in sorted(set(target_fields) - set(source_fields)):
        field = target_fields[name]
        if field.nullable and field.default_expression is None:
            classification = CompatibilityClass.BACKWARD
            message = "nullable field without default was added"
        elif field.default_expression is not None:
            classification = CompatibilityClass.BACKWARD
            message = "field with deterministic default was added"
        else:
            classification = CompatibilityClass.BREAKING
            message = "required field without default was added"
        _change(changes, f"fields.{name}", classification, message)
    for name in sorted(set(source_fields) & set(target_fields)):
        changes.extend(_compare_field(name, source_fields[name], target_fields[name]))
    source_indexes = {item.name: item for item in source.indexes}
    target_indexes = {item.name: item for item in target.indexes}
    for name in sorted(set(target_indexes) - set(source_indexes)):
        _change(
            changes,
            f"indexes.{name}",
            CompatibilityClass.DATA_COMPATIBLE,
            "index was added and requires Adapter migration",
        )
    for name in sorted(set(source_indexes) - set(target_indexes)):
        _change(
            changes,
            f"indexes.{name}",
            CompatibilityClass.CONDITIONAL,
            "index was removed and may invalidate Query guarantees",
        )
    for name in sorted(set(source_indexes) & set(target_indexes)):
        if source_indexes[name].to_dict() != target_indexes[name].to_dict():
            _change(
                changes,
                f"indexes.{name}",
                CompatibilityClass.DATA_COMPATIBLE,
                "index definition changed and requires Adapter migration",
            )
    _compare_profiles(source, target, changes)
    for path, left, right in (
        ("constraints", source.constraints, target.constraints),
        ("consistency", source.consistency, target.consistency),
        ("retentionLabel", source.retention_label, target.retention_label),
        ("auditPolicy", source.audit_policy, target.audit_policy),
        ("lineagePolicy", source.lineage_policy, target.lineage_policy),
        ("cachePolicy", source.cache_policy, target.cache_policy),
        ("extensions", source.extensions, target.extensions),
    ):
        if left != right and not any(item.path == path for item in changes):
            classification = (
                CompatibilityClass.CONDITIONAL
                if path in {"constraints", "consistency"}
                else CompatibilityClass.METADATA
            )
            _change(changes, path, classification, f"{path} metadata changed")
    if not changes:
        classification = CompatibilityClass.IDENTICAL
    else:
        classification = max(
            (item.classification for item in changes),
            key=_SEVERITY.__getitem__,
        )
    activation = {
        CompatibilityClass.IDENTICAL: "no-op",
        CompatibilityClass.METADATA: "metadata activation",
        CompatibilityClass.BACKWARD: "metadata activation; optional backfill or index migration",
        CompatibilityClass.DATA_COMPATIBLE: "Adapter migration",
        CompatibilityClass.CONDITIONAL: "Adapter proof and reviewed migration",
        CompatibilityClass.BREAKING: (
            "explicit transform, precondition validation, and reviewed migration"
        ),
    }[classification]
    return CompatibilityReport(
        source.fingerprint,
        target.fingerprint,
        classification,
        activation,
        tuple(sorted(changes)),
    )


def _compare_field(
    name: str,
    source: FieldDefinition,
    target: FieldDefinition,
) -> list[SchemaChange]:
    changes: list[SchemaChange] = []
    path = f"fields.{name}"
    if source.logical_type != target.logical_type:
        if _is_numeric_widening(source, target):
            classification = CompatibilityClass.CONDITIONAL
            message = "numeric type widened and requires Adapter proof"
        elif (
            source.logical_type.kind is LogicalKind.ENUM
            and target.logical_type.kind is LogicalKind.ENUM
            and set(source.logical_type.enum_values) <= set(target.logical_type.enum_values)
        ):
            classification = CompatibilityClass.BACKWARD
            message = "enum values were added"
        else:
            classification = CompatibilityClass.BREAKING
            message = "logical type narrowed or changed"
        _change(changes, f"{path}.logicalType", classification, message)
    if source.cardinality is not target.cardinality:
        _change(
            changes,
            f"{path}.cardinality",
            CompatibilityClass.BREAKING,
            "field cardinality changed",
        )
    if source.nullable != target.nullable:
        classification = (
            CompatibilityClass.BACKWARD if target.nullable else CompatibilityClass.BREAKING
        )
        _change(
            changes,
            f"{path}.nullable",
            classification,
            "field became nullable" if target.nullable else "field became required",
        )
    if source.default_expression != target.default_expression:
        _change(
            changes,
            f"{path}.defaultExpression",
            CompatibilityClass.CONDITIONAL,
            "default expression changed",
        )
    if source.mutable != target.mutable:
        _change(
            changes,
            f"{path}.mutable",
            (CompatibilityClass.BREAKING if not target.mutable else CompatibilityClass.BACKWARD),
            "field became immutable" if not target.mutable else "field became mutable",
        )
    if source.constraints != target.constraints:
        _change(
            changes,
            f"{path}.constraints",
            CompatibilityClass.CONDITIONAL,
            "field constraints changed",
        )
    descriptive_source = (
        source.sensitivity_labels,
        source.documentation,
        source.annotations,
    )
    descriptive_target = (
        target.sensitivity_labels,
        target.documentation,
        target.annotations,
    )
    if descriptive_source != descriptive_target:
        _change(
            changes,
            f"{path}.metadata",
            CompatibilityClass.METADATA,
            "field metadata changed",
        )
    return changes


def _is_numeric_widening(source: FieldDefinition, target: FieldDefinition) -> bool:
    integer_width = {
        LogicalKind.INT8: 8,
        LogicalKind.INT16: 16,
        LogicalKind.INT32: 32,
        LogicalKind.INT64: 64,
    }
    left = source.logical_type.kind
    right = target.logical_type.kind
    if left in integer_width and right in integer_width:
        return integer_width[right] > integer_width[left]
    if left is LogicalKind.DECIMAL and right is LogicalKind.DECIMAL:
        return (
            target.logical_type.precision >= source.logical_type.precision  # type: ignore[operator]
            and target.logical_type.scale >= source.logical_type.scale  # type: ignore[operator]
        )
    return False


def _compare_profiles(
    source: SchemaDocument,
    target: SchemaDocument,
    changes: list[SchemaChange],
) -> None:
    source_profile = source.profile
    target_profile = target.profile
    if source_profile == target_profile:
        return
    if isinstance(source_profile, RelationProfile) or isinstance(target_profile, RelationProfile):
        classification = CompatibilityClass.BREAKING
        message = "Relation endpoint declaration changed"
    elif source_profile is None or target_profile is None:
        classification = CompatibilityClass.BREAKING
        message = "semantic profile was added or removed"
    elif source_profile.kind is not target_profile.kind:
        classification = CompatibilityClass.BREAKING
        message = "semantic profile kind changed"
    else:
        classification = CompatibilityClass.CONDITIONAL
        message = "semantic profile metadata changed"
    _change(changes, "extensions.org.meridian.profile/v1", classification, message)


def _change(
    target: list[SchemaChange],
    path: str,
    classification: CompatibilityClass,
    message: str,
) -> None:
    target.append(SchemaChange(path, classification, message))


__all__ = [
    "CompatibilityClass",
    "CompatibilityReport",
    "SchemaChange",
    "classify_compatibility",
]
