# SPDX-License-Identifier: Apache-2.0
"""Portable Schema, profile, Record, reference, and value validation."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Never, Protocol, cast

from .canonical import FrozenJson, canonical_json_bytes, freeze_json, normalize_unicode_name
from .errors import (
    InvalidDefinition,
    InvalidRelationProfile,
    RecordValidationFailed,
    ReferenceViolation,
    UnsupportedSemantic,
)
from .profiles import (
    CacheProfile,
    DocumentProfile,
    FullTextProfile,
    GeospatialProfile,
    KeyValueProfile,
    ObjectProfile,
    RelationalProfile,
    RelationProfile,
    SemanticKind,
    TimeSeriesProfile,
)
from .resources import (
    CatalogName,
    ObjectReference,
    RecordReference,
    ResourceReference,
    normalize_timestamp,
    validate_base64url,
)
from .schemas import (
    Cardinality,
    FieldDefinition,
    LogicalKind,
    SchemaDocument,
    decimal_precision_scale,
)

FIELD_REMOVAL: Mapping[str, str] = MappingProxyType({"$meridian": "remove"})
_DURATION_RE = re.compile(r"^-?P(?=\d|T\d)(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?$")


class ReferenceResolver(Protocol):
    def exists(self, reference: RecordReference) -> bool: ...


def validate_schema(document: SchemaDocument) -> SchemaDocument:
    """Validate portable structure in the normative publication order."""

    expected_catalog = (
        CatalogName.OBJECT
        if document.semantic_kind
        in {SemanticKind.OBJECT, SemanticKind.ARTIFACT, SemanticKind.MEDIA}
        else CatalogName.CACHE
        if document.semantic_kind is SemanticKind.CACHE
        else CatalogName.STRUCTURED
    )
    if document.ref.catalog is not expected_catalog:
        _invalid(
            document,
            f"semanticKind {document.semantic_kind.value!r} belongs to the "
            f"{expected_catalog.value!r} Catalog",
            "schema.catalog-ownership",
        )
    fields = document.field_map
    for identity in document.identity:
        item = fields.get(identity)
        if item is None:
            _invalid(document, f"identity field {identity!r} is not defined", "identity.fields")
        if item.cardinality is Cardinality.MANY or not item.logical_type.scalar or item.nullable:
            _invalid(
                document,
                f"identity field {identity!r} must be a required scalar",
                "identity.scalar-required",
            )
    if (
        document.semantic_kind
        not in {
            SemanticKind.OBJECT,
            SemanticKind.ARTIFACT,
            SemanticKind.MEDIA,
        }
        and not document.identity
    ):
        _invalid(document, "Schema identity cannot be empty", "identity.required")
    for item in document.fields:
        _validate_field_definition(document, item)
    for index in document.indexes:
        missing = set(index.fields) - set(fields)
        if missing:
            _invalid(
                document,
                f"index {index.name!r} references unknown fields {sorted(missing)!r}",
                "indexes.fields",
            )
        _validate_index_types(document, index.kind, index.fields)
    profile = document.profile
    if profile is None:
        if document.semantic_kind not in {SemanticKind.RELATIONAL, SemanticKind.OBJECT}:
            _invalid(
                document, "specialized semantic kind requires profile metadata", "profile.required"
            )
        return document
    profile_kind = profile.kind
    if isinstance(profile, ObjectProfile):
        compatible = document.semantic_kind in {
            SemanticKind.OBJECT,
            SemanticKind.ARTIFACT,
            SemanticKind.MEDIA,
        }
    else:
        compatible = profile_kind is document.semantic_kind
    if not compatible:
        _invalid(
            document,
            f"profile {profile_kind.value!r} does not match semanticKind "
            f"{document.semantic_kind.value!r}",
            "profile.kind",
        )
    _validate_profile(document, profile)
    return document


def validate_record(
    document: SchemaDocument,
    values: Mapping[str, object],
    *,
    partial: bool = False,
    original: Mapping[str, object] | None = None,
    reference_resolver: ReferenceResolver | None = None,
) -> Mapping[str, FrozenJson]:
    """Normalize and validate a Record mapping without engine-specific behavior."""

    validate_schema(document)
    normalized_input: dict[str, object] = {}
    for raw_name, value in values.items():
        name = normalize_unicode_name(raw_name, "Record field name")
        if name in normalized_input:
            raise RecordValidationFailed(
                f"duplicate normalized Record field {name!r}",
                requirement="record.unique-fields",
                logical_references=(document.ref.canonical,),
                resource_ref=document.ref.schema_id,
            )
        normalized_input[name] = value
    unknown = set(normalized_input) - set(document.field_map)
    if unknown:
        raise RecordValidationFailed(
            f"Record contains unknown fields {sorted(unknown)!r}",
            requirement="record.known-fields",
            logical_references=(document.ref.canonical,),
            resource_ref=document.ref.schema_id,
        )
    result: dict[str, FrozenJson] = {}
    original_values = original or {}
    for field_name, raw in normalized_input.items():
        definition = document.field_map[field_name]
        if partial and not definition.mutable:
            previous = original_values.get(field_name, object())
            if previous != raw:
                _record_error(
                    document, field_name, "immutable field cannot be changed", "field.mutable"
                )
        if _is_removal(raw):
            if not partial:
                _record_error(
                    document, field_name, "field removal is valid only in patch", "patch.remove"
                )
            if not definition.nullable and definition.default_expression is None:
                _record_error(
                    document,
                    field_name,
                    "required field cannot be removed",
                    "field.required",
                )
            continue
        result[field_name] = _validate_field_value(
            document,
            definition,
            raw,
            reference_resolver=reference_resolver,
        )
    if not partial:
        pending = {
            item.name: item
            for item in document.fields
            if item.name not in result and item.default_expression is not None
        }
        while pending:
            progressed = False
            for name, definition in tuple(pending.items()):
                expression = definition.default_expression
                if expression is None:
                    raise AssertionError("pending default invariant was not preserved")
                try:
                    default = cast(object, expression.evaluate(result))
                except KeyError:
                    continue
                result[name] = _validate_field_value(
                    document,
                    definition,
                    default,
                    reference_resolver=reference_resolver,
                )
                del pending[name]
                progressed = True
            if not progressed:
                unresolved = sorted(pending)
                raise RecordValidationFailed(
                    f"default expressions have unresolved or cyclic dependencies: {unresolved!r}",
                    requirement="defaults.acyclic",
                    logical_references=(document.ref.canonical,),
                    resource_ref=document.ref.schema_id,
                )
        for definition in document.fields:
            if definition.name not in result and not definition.nullable:
                _record_error(
                    document,
                    definition.name,
                    "required field is missing",
                    "field.required",
                )
        for identity in document.identity:
            if identity not in result or result[identity] is None:
                _record_error(document, identity, "identity value is missing", "identity.required")
    return MappingProxyType(dict(sorted(result.items())))


def validate_patch(
    document: SchemaDocument,
    original: Mapping[str, object],
    changes: Mapping[str, object],
    *,
    reference_resolver: ReferenceResolver | None = None,
) -> Mapping[str, FrozenJson]:
    patch = validate_record(
        document,
        changes,
        partial=True,
        original=original,
        reference_resolver=reference_resolver,
    )
    merged = dict(original)
    for raw_name, value in changes.items():
        name = normalize_unicode_name(raw_name, "Record field name")
        if _is_removal(value):
            merged.pop(name, None)
        else:
            merged[name] = patch[name]
    validate_record(document, merged, reference_resolver=reference_resolver)
    return patch


def _validate_field_definition(document: SchemaDocument, item: FieldDefinition) -> None:
    allowed_constraints = {
        "allowedCollections",
        "exclusiveMax",
        "exclusiveMin",
        "max",
        "maxItems",
        "maxLength",
        "mediaTypes",
        "min",
        "minItems",
        "minLength",
        "pattern",
        "uniqueItems",
    }
    unsupported = set(item.constraints) - allowed_constraints
    if unsupported:
        raise UnsupportedSemantic(
            f"field {item.name!r} has unsupported portable constraints {sorted(unsupported)!r}",
            requirement="constraints.portable",
            logical_references=(document.ref.canonical,),
            resource_ref=document.ref.schema_id,
        )
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        value = item.constraints.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            _invalid(
                document,
                f"field {item.name!r} {key} must be a non-negative integer",
                f"constraint.{key}",
            )
    for minimum, maximum in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if (
            minimum in item.constraints
            and maximum in item.constraints
            and cast(int, item.constraints[minimum]) > cast(int, item.constraints[maximum])
        ):
            _invalid(
                document,
                f"field {item.name!r} {minimum} exceeds {maximum}",
                "constraint.bounds",
            )
    for key in ("min", "max", "exclusiveMin", "exclusiveMax"):
        if key in item.constraints:
            try:
                parsed_bound = Decimal(str(item.constraints[key]))
            except InvalidOperation as exc:
                _invalid(
                    document, f"field {item.name!r} {key} is not numeric", f"constraint.{key}", exc
                )
            if not parsed_bound.is_finite():
                _invalid(document, f"field {item.name!r} {key} must be finite", f"constraint.{key}")
    unique_items = item.constraints.get("uniqueItems")
    if unique_items is not None and not isinstance(unique_items, bool):
        _invalid(document, f"field {item.name!r} uniqueItems must be boolean", "constraint.unique")
    string_constraints = {"minLength", "maxLength", "pattern"} & set(item.constraints)
    if string_constraints and item.logical_type.kind is not LogicalKind.STRING:
        _invalid(
            document,
            f"field {item.name!r} string constraints require a string logical type",
            "constraint.applicability",
        )
    item_constraints = {"minItems", "maxItems", "uniqueItems"} & set(item.constraints)
    if item_constraints and item.cardinality is not Cardinality.MANY:
        _invalid(
            document,
            f"field {item.name!r} item constraints require cardinality many",
            "constraint.applicability",
        )
    numeric_constraints = {"min", "max", "exclusiveMin", "exclusiveMax"} & set(item.constraints)
    if numeric_constraints and item.logical_type.kind not in {
        LogicalKind.INT8,
        LogicalKind.INT16,
        LogicalKind.INT32,
        LogicalKind.INT64,
        LogicalKind.DECIMAL,
        LogicalKind.FLOAT64,
    }:
        _invalid(
            document,
            f"field {item.name!r} numeric constraints require a numeric logical type",
            "constraint.applicability",
        )
    allowed_collections = item.constraints.get("allowedCollections")
    if allowed_collections is not None:
        if item.logical_type.kind is not LogicalKind.RECORD_REF:
            _invalid(
                document,
                f"field {item.name!r} allowedCollections requires RecordRef",
                "constraint.allowed-collections",
            )
        if not isinstance(allowed_collections, tuple) or not allowed_collections:
            _invalid(
                document,
                f"field {item.name!r} allowedCollections must be a non-empty array",
                "constraint.allowed-collections",
            )
        try:
            for reference in allowed_collections:
                if not isinstance(reference, Mapping):
                    raise TypeError("Collection reference must be an object")
                ResourceReference.parse(reference, catalog="structured")
        except (TypeError, ValueError) as exc:
            _invalid(
                document,
                f"field {item.name!r} has invalid allowedCollections",
                "constraint.allowed-collections",
                exc,
            )
    media_types = item.constraints.get("mediaTypes")
    if media_types is not None:
        if item.logical_type.kind is not LogicalKind.OBJECT_REF:
            _invalid(
                document,
                f"field {item.name!r} mediaTypes requires ObjectRef",
                "constraint.media-types",
            )
        if not isinstance(media_types, tuple) or not media_types:
            _invalid(
                document,
                f"field {item.name!r} mediaTypes must be a non-empty array",
                "constraint.media-types",
            )
        if any(
            not isinstance(media_type, str)
            or "/" not in media_type
            or any(character.isspace() for character in media_type)
            for media_type in media_types
        ):
            _invalid(
                document,
                f"field {item.name!r} contains an invalid mediaTypes entry",
                "constraint.media-types",
            )
    if "pattern" in item.constraints:
        pattern = item.constraints["pattern"]
        if not isinstance(pattern, str) or len(pattern.encode("utf-8")) > 4096:
            _invalid(document, f"field {item.name!r} pattern is not bounded", "constraint.pattern")
        if "(?" in pattern or re.search(r"\\[1-9]", pattern) is not None:
            raise UnsupportedSemantic(
                f"field {item.name!r} pattern uses a non-portable regex construct",
                requirement="constraint.pattern-portable",
                logical_references=(document.ref.canonical,),
                resource_ref=document.ref.schema_id,
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            _invalid(
                document,
                f"field {item.name!r} pattern is invalid: {exc.msg}",
                "constraint.pattern",
            )
    if item.default_expression is not None:
        try:
            if item.default_expression.kind == "literal":
                _validate_field_value(
                    document,
                    item,
                    item.default_expression.arguments,
                    reference_resolver=None,
                )
        except RecordValidationFailed as exc:
            _invalid(document, f"field {item.name!r} default is invalid", "default.type", exc)


def _validate_index_types(document: SchemaDocument, kind: str, fields: tuple[str, ...]) -> None:
    definitions = [document.field_map[name] for name in fields]
    if kind == "full-text" and any(
        item.logical_type.kind is not LogicalKind.STRING for item in definitions
    ):
        _invalid(document, "full-text indexes require string fields", "index.full-text")
    if kind == "geospatial" and any(
        item.logical_type.kind is not LogicalKind.WGS84_POINT for item in definitions
    ):
        _invalid(document, "geospatial indexes require WGS84 point fields", "index.geospatial")
    if kind == "relation-endpoint" and any(
        item.logical_type.kind is not LogicalKind.RECORD_REF for item in definitions
    ):
        _invalid(document, "relation endpoint indexes require RecordRef fields", "index.relation")


def _validate_profile(document: SchemaDocument, profile: object) -> None:
    fields = document.field_map
    if isinstance(profile, RelationalProfile):
        for field_set in (*profile.alternate_keys, *profile.unique_fields):
            _require_fields(document, field_set)
        return
    if isinstance(profile, DocumentProfile):
        _require_kind(document, profile.body_field, allowed={LogicalKind.JSON})
        return
    if isinstance(profile, KeyValueProfile):
        _require_fields(document, (profile.key_field, profile.value_field))
        key_field = fields[profile.key_field]
        if (
            not key_field.logical_type.scalar
            or key_field.nullable
            or key_field.cardinality is Cardinality.MANY
        ):
            _invalid(document, "key-value key must be a required scalar", "profile.key-value.key")
        if profile.expires_at_field is not None:
            _require_kind(
                document,
                profile.expires_at_field,
                allowed={LogicalKind.UTC_TIMESTAMP, LogicalKind.DURATION},
            )
        return
    if isinstance(profile, FullTextProfile):
        _require_kind(document, *profile.source_fields, allowed={LogicalKind.STRING})
        _require_fields(document, (*profile.facets, *profile.highlights))
        if profile.normalized_field is not None:
            _require_kind(document, profile.normalized_field, allowed={LogicalKind.STRING})
        return
    if isinstance(profile, GeospatialProfile):
        _require_kind(document, *profile.point_fields, allowed={LogicalKind.WGS84_POINT})
        return
    if isinstance(profile, TimeSeriesProfile):
        _require_kind(
            document,
            profile.timestamp_field,
            allowed={LogicalKind.UTC_TIMESTAMP},
        )
        _require_fields(document, (*profile.series_identity, *profile.dimensions))
        _require_kind(
            document,
            *profile.measurements,
            allowed={
                LogicalKind.INT8,
                LogicalKind.INT16,
                LogicalKind.INT32,
                LogicalKind.INT64,
                LogicalKind.DECIMAL,
                LogicalKind.FLOAT64,
            },
        )
        if profile.exemplar_field is not None:
            _require_kind(
                document,
                profile.exemplar_field,
                allowed={LogicalKind.RECORD_REF},
            )
        return
    if isinstance(profile, RelationProfile):
        try:
            _require_kind(
                document,
                profile.source_field,
                profile.target_field,
                allowed={LogicalKind.RECORD_REF},
            )
        except InvalidDefinition as exc:
            raise InvalidRelationProfile(
                str(exc),
                requirement="relation.endpoint-fields",
                logical_references=(document.ref.canonical,),
                resource_ref=document.ref.schema_id,
            ) from exc
        endpoint_indexes = {
            index.fields[0]
            for index in document.indexes
            if index.kind == "relation-endpoint" and len(index.fields) == 1
        }
        required = {profile.source_field, profile.target_field}
        if not required <= endpoint_indexes:
            raise InvalidRelationProfile(
                "Relation endpoint fields require individual relation-endpoint indexes",
                requirement="relation.endpoint-indexes",
                logical_references=(document.ref.canonical,),
                resource_ref=document.ref.schema_id,
            )
        for field_name, allowed_collections in (
            (profile.source_field, profile.source_collections),
            (profile.target_field, profile.target_collections),
        ):
            constraint = fields[field_name].constraints.get("allowedCollections")
            if constraint is None:
                continue
            declared = {
                ResourceReference.parse(cast(Mapping[str, object], item))
                for item in cast(tuple[FrozenJson, ...], constraint)
            }
            if declared != set(allowed_collections):
                raise InvalidRelationProfile(
                    f"Relation field {field_name!r} allowedCollections disagrees with profile",
                    requirement="relation.endpoint-collections",
                    logical_references=(document.ref.canonical,),
                    resource_ref=document.ref.schema_id,
                )
        return
    if isinstance(profile, ObjectProfile):
        if profile.profile == "artifact" and not document.lineage_policy:
            _invalid(
                document, "artifact profile requires lineage policy", "object.artifact.lineage"
            )
        return
    if isinstance(profile, CacheProfile):
        return
    raise UnsupportedSemantic(
        f"unsupported semantic profile {type(profile).__name__}",
        requirement="profile.supported",
        logical_references=(document.ref.canonical,),
        resource_ref=document.ref.schema_id,
    )


def _require_fields(document: SchemaDocument, names: Sequence[str]) -> None:
    missing = set(names) - set(document.field_map)
    if missing:
        _invalid(
            document, f"profile references unknown fields {sorted(missing)!r}", "profile.fields"
        )


def _require_kind(
    document: SchemaDocument,
    *names: str,
    allowed: set[LogicalKind],
) -> None:
    _require_fields(document, names)
    invalid = [name for name in names if document.field_map[name].logical_type.kind not in allowed]
    if invalid:
        _invalid(
            document,
            f"profile fields {invalid!r} have incompatible logical types",
            "profile.field-types",
        )


def _validate_field_value(
    document: SchemaDocument,
    definition: FieldDefinition,
    value: object,
    *,
    reference_resolver: ReferenceResolver | None,
) -> FrozenJson:
    if value is None:
        if not definition.nullable:
            _record_error(document, definition.name, "null is not allowed", "field.nullable")
        return None
    if definition.cardinality is Cardinality.MANY:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            _record_error(document, definition.name, "value must be an array", "field.cardinality")
        array_result = tuple(
            _validate_scalar(document, definition, item, reference_resolver) for item in value
        )
        _validate_constraints(document, definition, array_result)
        return array_result
    scalar_result = _validate_scalar(document, definition, value, reference_resolver)
    _validate_constraints(document, definition, scalar_result)
    return scalar_result


def _validate_scalar(
    document: SchemaDocument,
    definition: FieldDefinition,
    value: object,
    reference_resolver: ReferenceResolver | None,
) -> FrozenJson:
    kind = definition.logical_type.kind
    if kind is LogicalKind.BOOLEAN:
        if not isinstance(value, bool):
            _type_error(document, definition, "boolean")
        return value
    if kind in {LogicalKind.INT8, LogicalKind.INT16, LogicalKind.INT32, LogicalKind.INT64}:
        if isinstance(value, bool) or not isinstance(value, int):
            _type_error(document, definition, "signed integer")
        bits = {
            LogicalKind.INT8: 8,
            LogicalKind.INT16: 16,
            LogicalKind.INT32: 32,
            LogicalKind.INT64: 64,
        }[kind]
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            _record_error(
                document, definition.name, f"integer exceeds {bits}-bit range", "type.range"
            )
        return value
    if kind is LogicalKind.DECIMAL:
        if not isinstance(value, str):
            _type_error(document, definition, "decimal string")
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            _type_error(document, definition, "decimal string")
        if not parsed.is_finite():
            _type_error(document, definition, "finite decimal string")
        precision, scale = decimal_precision_scale(parsed)
        if precision > cast(int, definition.logical_type.precision) or scale > cast(
            int, definition.logical_type.scale
        ):
            _record_error(
                document, definition.name, "decimal precision or scale overflow", "type.decimal"
            )
        return format(parsed, "f")
    if kind is LogicalKind.FLOAT64:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _type_error(document, definition, "finite float64")
        result = float(value)
        if not math.isfinite(result):
            _type_error(document, definition, "finite float64")
        return result
    if kind is LogicalKind.STRING:
        if not isinstance(value, str):
            _type_error(document, definition, "UTF-8 string")
        return value
    if kind is LogicalKind.BYTES:
        try:
            return validate_base64url(value)
        except (TypeError, ValueError) as exc:
            _record_error(document, definition.name, str(exc), "type.bytes", cause=exc)
    if kind is LogicalKind.UUID:
        if not isinstance(value, str):
            _type_error(document, definition, "UUID string")
        try:
            parsed_uuid = uuid.UUID(value)
        except ValueError as exc:
            _record_error(document, definition.name, "invalid UUID", "type.uuid", cause=exc)
        if str(parsed_uuid) != value:
            _record_error(document, definition.name, "UUID must use canonical form", "type.uuid")
        return str(parsed_uuid)
    if kind is LogicalKind.UTC_TIMESTAMP:
        try:
            return normalize_timestamp(cast(str, value))
        except (TypeError, ValueError) as exc:
            _record_error(document, definition.name, str(exc), "type.timestamp", cause=exc)
    if kind is LogicalKind.DATE:
        if not isinstance(value, str):
            _type_error(document, definition, "ISO date")
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            _record_error(document, definition.name, "invalid ISO date", "type.date", cause=exc)
        return value
    if kind is LogicalKind.DURATION:
        if not isinstance(value, str) or _DURATION_RE.fullmatch(value) is None:
            _type_error(document, definition, "ISO 8601 duration")
        return value
    if kind is LogicalKind.ENUM:
        if not isinstance(value, str) or value not in definition.logical_type.enum_values:
            _record_error(document, definition.name, "value is not in the enum", "type.enum")
        return value
    if kind is LogicalKind.JSON:
        try:
            return freeze_json(value)
        except (TypeError, ValueError) as exc:
            _record_error(document, definition.name, str(exc), "type.json", cause=exc)
    if kind is LogicalKind.RECORD_REF:
        if not isinstance(value, Mapping):
            _type_error(document, definition, "RecordRef object")
        try:
            record_reference = RecordReference.from_mapping(value)
        except (TypeError, ValueError) as exc:
            _record_error(document, definition.name, str(exc), "type.record-ref", cause=exc)
        allowed = definition.constraints.get("allowedCollections")
        if allowed is not None:
            allowed_refs = {
                ResourceReference.parse(cast(Mapping[str, object], item))
                for item in cast(tuple[FrozenJson, ...], allowed)
            }
            if record_reference.collection_ref not in allowed_refs:
                raise ReferenceViolation(
                    f"RecordRef in field {definition.name!r} targets a disallowed Collection",
                    requirement="reference.allowed-endpoint",
                    logical_references=(
                        document.ref.canonical,
                        record_reference.collection_ref.canonical,
                    ),
                    resource_ref=document.ref.schema_id,
                )
        if reference_resolver is not None and not reference_resolver.exists(record_reference):
            raise ReferenceViolation(
                f"RecordRef in field {definition.name!r} does not exist",
                requirement="reference.exists",
                logical_references=(
                    document.ref.canonical,
                    record_reference.collection_ref.canonical,
                ),
                resource_ref=document.ref.schema_id,
            )
        return freeze_json(record_reference.to_dict())
    if kind is LogicalKind.OBJECT_REF:
        if not isinstance(value, Mapping):
            _type_error(document, definition, "ObjectRef object")
        try:
            resource = value["resourceRef"]
            if not isinstance(resource, Mapping):
                raise TypeError("ObjectRef resourceRef must be an object")
            object_reference = ObjectReference(
                ResourceReference.parse(resource),
                cast(str, value["objectId"]),
                cast(str | None, value.get("digest")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            _record_error(document, definition.name, str(exc), "type.object-ref", cause=exc)
        return freeze_json(object_reference.to_dict())
    if kind is LogicalKind.WGS84_POINT:
        if not isinstance(value, Mapping) or set(value) != {"longitude", "latitude"}:
            _type_error(document, definition, "WGS84 point object")
        longitude = value["longitude"]
        latitude = value["latitude"]
        if (
            isinstance(longitude, bool)
            or isinstance(latitude, bool)
            or not isinstance(longitude, (int, float))
            or not isinstance(latitude, (int, float))
            or not math.isfinite(float(longitude))
            or not math.isfinite(float(latitude))
            or not -180 <= float(longitude) <= 180
            or not -90 <= float(latitude) <= 90
        ):
            _record_error(document, definition.name, "invalid WGS84 coordinate", "type.wgs84")
        return MappingProxyType({"latitude": float(latitude), "longitude": float(longitude)})
    raise AssertionError(f"unhandled logical type: {kind.value}")


def _validate_constraints(
    document: SchemaDocument,
    definition: FieldDefinition,
    value: FrozenJson,
) -> None:
    constraints = definition.constraints
    if isinstance(value, (str, tuple)):
        minimum = constraints.get("minLength" if isinstance(value, str) else "minItems")
        maximum = constraints.get("maxLength" if isinstance(value, str) else "maxItems")
        if minimum is not None and len(value) < cast(int, minimum):
            _record_error(
                document, definition.name, "value is shorter than minimum", "constraint.min"
            )
        if maximum is not None and len(value) > cast(int, maximum):
            _record_error(
                document, definition.name, "value exceeds maximum length", "constraint.max"
            )
    if (
        isinstance(value, str)
        and "pattern" in constraints
        and re.search(cast(str, constraints["pattern"]), value) is None
    ):
        _record_error(
            document, definition.name, "value does not match pattern", "constraint.pattern"
        )
    if isinstance(value, tuple) and constraints.get("uniqueItems") is True:
        fingerprints = [canonical_json_bytes(item) for item in value]
        if len(set(fingerprints)) != len(fingerprints):
            _record_error(
                document, definition.name, "array items must be unique", "constraint.unique"
            )
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        comparable: Decimal | None = None
        if definition.logical_type.kind in {
            LogicalKind.INT8,
            LogicalKind.INT16,
            LogicalKind.INT32,
            LogicalKind.INT64,
            LogicalKind.DECIMAL,
            LogicalKind.FLOAT64,
        }:
            comparable = Decimal(str(value))
        if comparable is not None:
            for key in ("min", "max", "exclusiveMin", "exclusiveMax"):
                if key not in constraints:
                    continue
                limit = Decimal(str(constraints[key]))
                failed = (
                    (key == "min" and comparable < limit)
                    or (key == "max" and comparable > limit)
                    or (key == "exclusiveMin" and comparable <= limit)
                    or (key == "exclusiveMax" and comparable >= limit)
                )
                if failed:
                    _record_error(
                        document,
                        definition.name,
                        f"numeric {key} constraint failed",
                        f"constraint.{key}",
                    )


def _is_removal(value: object) -> bool:
    return isinstance(value, Mapping) and dict(value) == dict(FIELD_REMOVAL)


def _type_error(document: SchemaDocument, definition: FieldDefinition, expected: str) -> Never:
    _record_error(document, definition.name, f"value must be {expected}", "field.logical-type")


def _record_error(
    document: SchemaDocument,
    field_name: str,
    message: str,
    requirement: str,
    *,
    cause: BaseException | None = None,
) -> Never:
    del cause
    raise RecordValidationFailed(
        f"field {field_name!r}: {message}",
        requirement=requirement,
        logical_references=(document.ref.canonical,),
        resource_ref=document.ref.schema_id,
    )


def _invalid(
    document: SchemaDocument,
    message: str,
    requirement: str,
    cause: BaseException | None = None,
) -> Never:
    del cause
    raise InvalidDefinition(
        message,
        requirement=requirement,
        logical_references=(document.ref.canonical,),
        resource_ref=document.ref.schema_id,
    )


__all__ = [
    "FIELD_REMOVAL",
    "ReferenceResolver",
    "validate_patch",
    "validate_record",
    "validate_schema",
]
