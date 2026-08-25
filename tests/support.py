# SPDX-License-Identifier: Apache-2.0
"""Deterministic builders shared by unit, integration, and conformance tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from meridian_storage.semantics import (
    PROFILE_EXTENSION_KEY,
    CatalogName,
    DefaultExpression,
    FieldDefinition,
    FrozenJson,
    IndexDefinition,
    LogicalKind,
    LogicalType,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
)

NOW = datetime(2026, 8, 25, 12, 34, 56, 123456, tzinfo=UTC)
NOW_TEXT = "2026-08-25T12:34:56.123456Z"
DIGEST = "sha256:" + "a" * 64
FINGERPRINT = "sha256:" + "b" * 64


def field(
    name: str,
    kind: LogicalKind = LogicalKind.STRING,
    *,
    nullable: bool = False,
    default: DefaultExpression | None = None,
    mutable: bool = True,
    constraints: Mapping[str, FrozenJson] | None = None,
    logical_type: LogicalType | None = None,
) -> FieldDefinition:
    return FieldDefinition(
        name,
        logical_type or LogicalType(kind),
        nullable=nullable,
        default_expression=default,
        mutable=mutable,
        constraints=constraints or {},
    )


def schema(
    version: str = "1.0.0",
    *,
    name: str = "customer",
    catalog: CatalogName = CatalogName.STRUCTURED,
    semantic_kind: SemanticKind = SemanticKind.RELATIONAL,
    fields: Sequence[FieldDefinition] | None = None,
    identity: Sequence[str] = ("id",),
    indexes: Sequence[IndexDefinition] = (),
    profile: Mapping[str, object] | None = None,
    extensions: Mapping[str, object] | None = None,
    lineage_policy: Mapping[str, object] | None = None,
) -> SchemaDocument:
    extension_values: dict[str, object] = dict(extensions or {})
    if profile is not None:
        extension_values[PROFILE_EXTENSION_KEY] = dict(profile)
    return SchemaDocument(
        SchemaReference(catalog, "example", name, version),
        semantic_kind,
        tuple(fields or (field("id"), field("name", nullable=True))),
        tuple(identity),
        indexes=tuple(indexes),
        extensions=extension_values,
        lineage_policy=lineage_policy or {},
    )


def schema_definition(
    *,
    semantic_kind: str = "relational",
    fields: Sequence[Mapping[str, object]] | None = None,
    identity: Sequence[str] = ("id",),
    extensions: Mapping[str, object] | None = None,
    indexes: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    return {
        "semanticKind": semantic_kind,
        "fields": list(
            fields
            or (
                {"name": "id", "logicalType": "string"},
                {"name": "name", "logicalType": "string", "nullable": True},
            )
        ),
        "identity": list(identity),
        "indexes": list(indexes),
        "extensions": dict(extensions or {}),
    }
