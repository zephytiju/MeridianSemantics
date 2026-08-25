# SPDX-License-Identifier: Apache-2.0
"""Canonical meridian.schema.v1 documents and logical field contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from meridian_storage.registry import SchemaDefinition as CoreSchemaDefinition

from .canonical import (
    FrozenJson,
    JsonValue,
    document_fingerprint,
    freeze_json,
    normalize_extension_key,
    normalize_unicode_name,
    thaw_json,
)
from .i18n import LocalizedText
from .profiles import ProfileDefinition, SemanticKind, profile_from_mapping
from .resources import CatalogName, SchemaReference

SCHEMA_FORMAT_VERSION = "meridian.schema.v1"
PROFILE_EXTENSION_KEY = "org.meridian.profile/v1"


class LogicalKind(StrEnum):
    BOOLEAN = "boolean"
    INT8 = "int8"
    INT16 = "int16"
    INT32 = "int32"
    INT64 = "int64"
    DECIMAL = "decimal"
    FLOAT64 = "float64"
    STRING = "string"
    BYTES = "bytes"
    UUID = "uuid"
    UTC_TIMESTAMP = "utcTimestamp"
    DATE = "date"
    DURATION = "duration"
    ENUM = "enum"
    JSON = "json"
    RECORD_REF = "recordRef"
    OBJECT_REF = "objectRef"
    WGS84_POINT = "wgs84Point"


_PARAMETERIZED_KINDS = {LogicalKind.DECIMAL, LogicalKind.ENUM}
_SCALAR_KINDS = {
    LogicalKind.BOOLEAN,
    LogicalKind.INT8,
    LogicalKind.INT16,
    LogicalKind.INT32,
    LogicalKind.INT64,
    LogicalKind.DECIMAL,
    LogicalKind.FLOAT64,
    LogicalKind.STRING,
    LogicalKind.BYTES,
    LogicalKind.UUID,
    LogicalKind.UTC_TIMESTAMP,
    LogicalKind.DATE,
    LogicalKind.DURATION,
    LogicalKind.ENUM,
}


@dataclass(frozen=True, slots=True)
class LogicalType:
    kind: LogicalKind
    precision: int | None = None
    scale: int | None = None
    enum_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        kind = LogicalKind(self.kind)
        object.__setattr__(self, "kind", kind)
        if kind is LogicalKind.DECIMAL:
            if (
                isinstance(self.precision, bool)
                or not isinstance(self.precision, int)
                or not 1 <= self.precision <= 1000
            ):
                raise ValueError("decimal precision must be between 1 and 1000")
            if (
                isinstance(self.scale, bool)
                or not isinstance(self.scale, int)
                or not 0 <= self.scale <= self.precision
            ):
                raise ValueError("decimal scale must be between 0 and precision")
            if self.enum_values:
                raise ValueError("decimal logical type cannot contain enum values")
        elif kind is LogicalKind.ENUM:
            values = tuple(
                normalize_unicode_name(item, "enum value", maximum=1024)
                for item in self.enum_values
            )
            if not values or len(set(values)) != len(values):
                raise ValueError("enum values must be non-empty and unique")
            object.__setattr__(self, "enum_values", values)
            if self.precision is not None or self.scale is not None:
                raise ValueError("enum logical type cannot contain decimal parameters")
        elif any(value is not None for value in (self.precision, self.scale)) or self.enum_values:
            raise ValueError(f"logical type {kind.value!r} does not accept parameters")

    @property
    def scalar(self) -> bool:
        return self.kind in _SCALAR_KINDS

    def to_wire(self) -> JsonValue:
        if self.kind not in _PARAMETERIZED_KINDS:
            return self.kind.value
        if self.kind is LogicalKind.DECIMAL:
            return {
                "kind": self.kind.value,
                "precision": cast(int, self.precision),
                "scale": cast(int, self.scale),
            }
        return {"kind": self.kind.value, "values": list(self.enum_values)}

    @classmethod
    def parse(cls, value: LogicalType | str | Mapping[str, object]) -> LogicalType:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            kind = LogicalKind(value)
            if kind in _PARAMETERIZED_KINDS:
                raise ValueError(f"logical type {kind.value!r} requires parameters")
            return cls(kind)
        if not isinstance(value, Mapping) or "kind" not in value:
            raise TypeError("logicalType must be a string or object")
        allowed = {"kind", "precision", "scale", "values"}
        if set(value) - allowed:
            raise ValueError(f"unknown logicalType fields: {sorted(set(value) - allowed)!r}")
        return cls(
            LogicalKind(cast(str, value["kind"])),
            precision=cast(int | None, value.get("precision")),
            scale=cast(int | None, value.get("scale")),
            enum_values=tuple(cast(Sequence[str], value.get("values", ()))),
        )


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


@dataclass(frozen=True, slots=True)
class DefaultExpression:
    """Bounded deterministic default expression; executable behavior is excluded."""

    kind: str
    arguments: FrozenJson

    def __post_init__(self) -> None:
        if self.kind not in {"literal", "field", "concat", "coalesce", "lower", "upper"}:
            raise ValueError(f"unsupported or non-deterministic default expression: {self.kind!r}")
        arguments = freeze_json(self.arguments)
        if self.kind == "field" and not isinstance(arguments, str):
            raise ValueError("field default expression requires a field-name string")
        if self.kind in {"concat", "coalesce"} and not isinstance(arguments, tuple):
            raise ValueError(f"{self.kind} default expression requires an argument array")
        if self.kind in {"lower", "upper"} and not isinstance(arguments, (str, Mapping)):
            raise ValueError(f"{self.kind} default expression requires a string expression")
        object.__setattr__(self, "arguments", arguments)

    @classmethod
    def from_mapping(cls, value: object) -> DefaultExpression:
        if not isinstance(value, Mapping):
            return cls("literal", freeze_json(value))
        if set(value) != {"kind", "arguments"}:
            raise ValueError("defaultExpression requires kind and arguments")
        return cls(cast(str, value["kind"]), freeze_json(value["arguments"]))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "arguments": thaw_json(self.arguments)}

    def evaluate(self, values: Mapping[str, FrozenJson]) -> FrozenJson:
        return _evaluate_default(self, values)


def _evaluate_default(
    expression: DefaultExpression | FrozenJson,
    values: Mapping[str, FrozenJson],
) -> FrozenJson:
    if not isinstance(expression, DefaultExpression):
        return expression
    if expression.kind == "literal":
        return expression.arguments
    if expression.kind == "field":
        name = cast(str, expression.arguments)
        if name not in values:
            raise KeyError(name)
        return values[name]
    if expression.kind in {"lower", "upper"}:
        value = _default_operand(expression.arguments, values)
        if not isinstance(value, str):
            raise ValueError(f"{expression.kind} default operand must resolve to string")
        return value.lower() if expression.kind == "lower" else value.upper()
    arguments = cast(tuple[FrozenJson, ...], expression.arguments)
    resolved = tuple(_default_operand(item, values) for item in arguments)
    if expression.kind == "coalesce":
        return next((item for item in resolved if item is not None), None)
    if not all(isinstance(item, str) for item in resolved):
        raise ValueError("concat default operands must resolve to strings")
    return "".join(cast(tuple[str, ...], resolved))


def _default_operand(value: FrozenJson, values: Mapping[str, FrozenJson]) -> FrozenJson:
    if isinstance(value, Mapping) and set(value) == {"kind", "arguments"}:
        return DefaultExpression.from_mapping(value).evaluate(values)
    return value


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    name: str
    logical_type: LogicalType
    cardinality: Cardinality = Cardinality.ONE
    nullable: bool = False
    default_expression: DefaultExpression | None = None
    mutable: bool = True
    constraints: Mapping[str, FrozenJson] = field(default_factory=dict)
    sensitivity_labels: tuple[str, ...] = ()
    documentation: LocalizedText | None = None
    annotations: Mapping[str, FrozenJson] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = normalize_unicode_name(self.name, "field name")
        logical_type = LogicalType.parse(self.logical_type)
        cardinality = Cardinality(self.cardinality)
        if not isinstance(self.nullable, bool) or not isinstance(self.mutable, bool):
            raise ValueError("Field nullable and mutable values must be booleans")
        constraints = MappingProxyType(
            {
                normalize_unicode_name(key, "field constraint", maximum=128): freeze_json(value)
                for key, value in sorted(self.constraints.items())
            }
        )
        labels = tuple(
            sorted(
                {
                    normalize_unicode_name(item, "sensitivity label", maximum=128)
                    for item in self.sensitivity_labels
                }
            )
        )
        if len(labels) != len(self.sensitivity_labels):
            raise ValueError("sensitivity labels must be unique")
        annotations = MappingProxyType(
            {
                normalize_extension_key(key): freeze_json(value)
                for key, value in sorted(self.annotations.items())
            }
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "logical_type", logical_type)
        object.__setattr__(self, "cardinality", cardinality)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "sensitivity_labels", labels)
        object.__setattr__(self, "annotations", annotations)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> FieldDefinition:
        allowed = {
            "name",
            "logicalType",
            "cardinality",
            "nullable",
            "defaultExpression",
            "mutable",
            "constraints",
            "sensitivityLabels",
            "documentation",
            "annotations",
        }
        required = {"name", "logicalType"}
        if required - set(value) or set(value) - allowed:
            raise ValueError("Field contains unknown or missing fields")
        default = value.get("defaultExpression")
        documentation = value.get("documentation")
        constraints = value.get("constraints", {})
        annotations = value.get("annotations", {})
        if not isinstance(constraints, Mapping) or not isinstance(annotations, Mapping):
            raise TypeError("Field constraints and annotations must be objects")
        return cls(
            name=cast(str, value["name"]),
            logical_type=LogicalType.parse(cast(str | Mapping[str, object], value["logicalType"])),
            cardinality=Cardinality(cast(str, value.get("cardinality", "one"))),
            nullable=cast(bool, value.get("nullable", False)),
            default_expression=None if default is None else DefaultExpression.from_mapping(default),
            mutable=cast(bool, value.get("mutable", True)),
            constraints=cast(Mapping[str, FrozenJson], constraints),
            sensitivity_labels=tuple(cast(Sequence[str], value.get("sensitivityLabels", ()))),
            documentation=(
                None if documentation is None else LocalizedText.from_mapping(documentation)
            ),
            annotations=cast(Mapping[str, FrozenJson], annotations),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "logicalType": self.logical_type.to_wire(),
            "cardinality": self.cardinality.value,
            "nullable": self.nullable,
            "defaultExpression": (
                None if self.default_expression is None else self.default_expression.to_dict()
            ),
            "mutable": self.mutable,
            "constraints": {key: thaw_json(value) for key, value in self.constraints.items()},
            "sensitivityLabels": list(self.sensitivity_labels),
            "documentation": (None if self.documentation is None else self.documentation.to_dict()),
            "annotations": {key: thaw_json(value) for key, value in self.annotations.items()},
        }


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    name: str
    kind: str
    fields: tuple[str, ...]
    unique: bool = False
    options: Mapping[str, FrozenJson] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = normalize_unicode_name(self.name, "index name")
        allowed = {
            "btree",
            "hash",
            "full-text",
            "geospatial",
            "time-series",
            "relation-endpoint",
        }
        if self.kind not in allowed:
            raise ValueError(f"unsupported portable index kind: {self.kind!r}")
        fields = tuple(normalize_unicode_name(item, "index field") for item in self.fields)
        if not fields or len(set(fields)) != len(fields):
            raise ValueError("Index fields must be non-empty and unique")
        options = MappingProxyType(
            {key: freeze_json(item) for key, item in sorted(self.options.items())}
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "options", options)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> IndexDefinition:
        allowed = {"name", "kind", "fields", "unique", "options"}
        if {"name", "kind", "fields"} - set(value) or set(value) - allowed:
            raise ValueError("Index contains unknown or missing fields")
        fields = value["fields"]
        options = value.get("options", {})
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
            raise TypeError("Index fields must be an array")
        if not isinstance(options, Mapping):
            raise TypeError("Index options must be an object")
        return cls(
            cast(str, value["name"]),
            cast(str, value["kind"]),
            tuple(cast(Sequence[str], fields)),
            cast(bool, value.get("unique", False)),
            cast(Mapping[str, FrozenJson], options),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "kind": self.kind,
            "fields": list(self.fields),
            "unique": self.unique,
            "options": {key: thaw_json(item) for key, item in self.options.items()},
        }


@dataclass(frozen=True, slots=True)
class SchemaDocument:
    ref: SchemaReference
    semantic_kind: SemanticKind
    fields: tuple[FieldDefinition, ...]
    identity: tuple[str, ...]
    constraints: tuple[Mapping[str, FrozenJson], ...] = ()
    indexes: tuple[IndexDefinition, ...] = ()
    consistency: str = "strong"
    retention_label: str = "default"
    audit_policy: Mapping[str, FrozenJson] = field(default_factory=dict)
    lineage_policy: Mapping[str, FrozenJson] = field(default_factory=dict)
    cache_policy: Mapping[str, FrozenJson] = field(default_factory=dict)
    extensions: Mapping[str, FrozenJson] = field(default_factory=dict)
    compatibility: Mapping[str, FrozenJson] = field(default_factory=dict)
    format_version: str = SCHEMA_FORMAT_VERSION

    def __post_init__(self) -> None:
        reference = SchemaReference.parse(self.ref).exact()
        kind = SemanticKind(self.semantic_kind)
        fields = tuple(sorted(self.fields, key=lambda item: item.name))
        if not fields:
            raise ValueError("Schema fields cannot be empty")
        if len({item.name for item in fields}) != len(fields):
            raise ValueError("Schema field names must be unique after NFC normalization")
        identity = tuple(normalize_unicode_name(item, "identity field") for item in self.identity)
        if len(set(identity)) != len(identity):
            raise ValueError("Schema identity fields must be unique")
        constraints = tuple(
            MappingProxyType({key: freeze_json(item) for key, item in sorted(value.items())})
            for value in self.constraints
        )
        indexes = tuple(sorted(self.indexes, key=lambda item: item.name))
        if len({item.name for item in indexes}) != len(indexes):
            raise ValueError("Schema index names must be unique")
        if self.consistency not in {"strong", "session", "eventual"}:
            raise ValueError("unsupported Schema consistency")
        retention = normalize_unicode_name(self.retention_label, "retention label", maximum=128)
        extensions = MappingProxyType(
            {
                normalize_extension_key(key): freeze_json(item)
                for key, item in sorted(self.extensions.items())
            }
        )
        if self.format_version != SCHEMA_FORMAT_VERSION:
            raise ValueError(f"formatVersion must be {SCHEMA_FORMAT_VERSION!r}")
        object.__setattr__(self, "ref", reference)
        object.__setattr__(self, "semantic_kind", kind)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "indexes", indexes)
        object.__setattr__(self, "retention_label", retention)
        for name in ("audit_policy", "lineage_policy", "cache_policy", "compatibility"):
            object.__setattr__(self, name, _mapping(getattr(self, name)))
        object.__setattr__(self, "extensions", extensions)

    @property
    def fingerprint(self) -> str:
        return document_fingerprint(
            cast(Mapping[str, object], self.to_dict(include_fingerprint=False))
        )

    @property
    def field_map(self) -> Mapping[str, FieldDefinition]:
        return MappingProxyType({item.name: item for item in self.fields})

    @property
    def profile(self) -> ProfileDefinition | None:
        value = self.extensions.get(PROFILE_EXTENSION_KEY)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("profile extension must be an object")
        return profile_from_mapping(cast(Mapping[str, object], value))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "formatVersion": self.format_version,
            "id": self.ref.schema_id,
            "version": cast(str, self.ref.version),
            "semanticKind": self.semantic_kind.value,
            "fields": [item.to_dict() for item in self.fields],
            "identity": list(self.identity),
            "constraints": [
                {key: thaw_json(item) for key, item in value.items()} for value in self.constraints
            ],
            "indexes": [item.to_dict() for item in self.indexes],
            "consistency": self.consistency,
            "retentionLabel": self.retention_label,
            "auditPolicy": _thaw(self.audit_policy),
            "lineagePolicy": _thaw(self.lineage_policy),
            "cachePolicy": _thaw(self.cache_policy),
            "extensions": _thaw(self.extensions),
            "compatibility": _thaw(self.compatibility),
        }
        if include_fingerprint:
            result["fingerprint"] = document_fingerprint(cast(Mapping[str, object], result))
        return result

    def to_core_definition(self) -> CoreSchemaDefinition:
        return CoreSchemaDefinition(
            self.ref.to_core(),
            cast(Mapping[str, JsonValue], self.to_dict()),
            extensions={"org.meridian.contract/source": "meridian.schema.v1"},
        )

    @classmethod
    def from_definition(
        cls,
        *,
        catalog: CatalogName | str,
        namespace: str,
        name: str,
        version: str,
        definition: Mapping[str, object],
    ) -> SchemaDocument:
        allowed = {
            "formatVersion",
            "id",
            "version",
            "semanticKind",
            "fields",
            "identity",
            "constraints",
            "indexes",
            "consistency",
            "retentionLabel",
            "auditPolicy",
            "lineagePolicy",
            "cachePolicy",
            "extensions",
            "compatibility",
            "fingerprint",
        }
        unknown = set(definition) - allowed
        if unknown:
            raise ValueError(f"unknown Schema fields: {sorted(unknown)!r}")
        reference = SchemaReference(CatalogName(catalog), namespace, name, version)
        if definition.get("formatVersion", SCHEMA_FORMAT_VERSION) != SCHEMA_FORMAT_VERSION:
            raise ValueError("Schema formatVersion does not match meridian.schema.v1")
        if definition.get("id", reference.schema_id) != reference.schema_id:
            raise ValueError("Schema id does not match its Catalog/Namespace/name address")
        if definition.get("version", version) != reference.version:
            raise ValueError("Schema version does not match publication request")
        if "semanticKind" not in definition or "fields" not in definition:
            raise ValueError("Schema definition requires semanticKind and fields")
        fields = _parse_fields(definition["fields"])
        indexes = _parse_indexes(definition.get("indexes", ()))
        constraints = _sequence_of_mappings(definition.get("constraints", ()), "constraints")
        identity_value = definition.get("identity", ())
        if not isinstance(identity_value, Sequence) or isinstance(identity_value, (str, bytes)):
            raise TypeError("Schema identity must be an array")
        policies: dict[str, Mapping[str, FrozenJson]] = {}
        for key in ("auditPolicy", "lineagePolicy", "cachePolicy", "compatibility"):
            value = definition.get(key, {})
            if not isinstance(value, Mapping):
                raise TypeError(f"Schema {key} must be an object")
            policies[key] = cast(Mapping[str, FrozenJson], value)
        extensions = definition.get("extensions", {})
        if not isinstance(extensions, Mapping):
            raise TypeError("Schema extensions must be an object")
        document = cls(
            ref=reference,
            semantic_kind=SemanticKind(cast(str, definition["semanticKind"])),
            fields=fields,
            identity=tuple(cast(Sequence[str], identity_value)),
            constraints=constraints,
            indexes=indexes,
            consistency=cast(str, definition.get("consistency", "strong")),
            retention_label=cast(str, definition.get("retentionLabel", "default")),
            audit_policy=policies["auditPolicy"],
            lineage_policy=policies["lineagePolicy"],
            cache_policy=policies["cachePolicy"],
            extensions=cast(Mapping[str, FrozenJson], extensions),
            compatibility=policies["compatibility"],
        )
        declared = definition.get("fingerprint")
        if declared is not None and declared != document.fingerprint:
            raise ValueError("declared Schema fingerprint does not match canonical content")
        return document


def _parse_fields(value: object) -> tuple[FieldDefinition, ...]:
    items: list[FieldDefinition] = []
    if isinstance(value, Mapping):
        for name, definition in value.items():
            if not isinstance(definition, Mapping):
                raise TypeError("Schema field definitions must be objects")
            item = dict(definition)
            if "name" in item and item["name"] != name:
                raise ValueError("Schema field mapping key does not match Field name")
            item["name"] = name
            items.append(FieldDefinition.from_mapping(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for definition in value:
            if not isinstance(definition, Mapping):
                raise TypeError("Schema field definitions must be objects")
            items.append(FieldDefinition.from_mapping(definition))
    else:
        raise TypeError("Schema fields must be an object or array")
    return tuple(items)


def _parse_indexes(value: object) -> tuple[IndexDefinition, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("Schema indexes must be an array")
    result: list[IndexDefinition] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError("Schema index definitions must be objects")
        result.append(IndexDefinition.from_mapping(item))
    return tuple(result)


def _sequence_of_mappings(
    value: object,
    name: str,
) -> tuple[Mapping[str, FrozenJson], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"Schema {name} must be an array")
    result: list[Mapping[str, FrozenJson]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError(f"Schema {name} entries must be objects")
        result.append(cast(Mapping[str, FrozenJson], item))
    return tuple(result)


def _mapping(value: Mapping[str, object]) -> Mapping[str, FrozenJson]:
    return MappingProxyType({key: freeze_json(item) for key, item in sorted(value.items())})


def _thaw(value: Mapping[str, FrozenJson]) -> dict[str, JsonValue]:
    return {key: thaw_json(item) for key, item in value.items()}


def decimal_precision_scale(value: Decimal) -> tuple[int, int]:
    if not value.is_finite():
        raise ValueError("decimal values must be finite")
    sign, digits, exponent = value.as_tuple()
    del sign
    scale = max(-cast(int, exponent), 0)
    precision = max(len(digits), scale + 1 if value == 0 else len(digits))
    return precision, scale


__all__ = [
    "PROFILE_EXTENSION_KEY",
    "SCHEMA_FORMAT_VERSION",
    "Cardinality",
    "DefaultExpression",
    "FieldDefinition",
    "IndexDefinition",
    "LogicalKind",
    "LogicalType",
    "SchemaDocument",
    "decimal_precision_scale",
]
