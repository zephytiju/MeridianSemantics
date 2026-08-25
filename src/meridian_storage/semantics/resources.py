# SPDX-License-Identifier: Apache-2.0
"""Immutable Catalog, Resource, Record, Object, and cache metadata values."""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from meridian_storage import ResourceRef as CoreResourceRef
from meridian_storage import SchemaRef as CoreSchemaRef

from .canonical import (
    FrozenJson,
    JsonValue,
    freeze_json,
    normalize_core_name,
    normalize_extension_key,
    normalize_unicode_name,
    thaw_json,
)

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STRUCTURED_PROFILES = {
    "relational",
    "document",
    "key-value",
    "search",
    "geospatial",
    "time-series",
    "relation",
}


class CatalogName(StrEnum):
    STRUCTURED = "structured"
    OBJECT = "object"
    CACHE = "cache"
    EVIDENCE = "evidence"
    STREAMING = "streaming"


class CatalogStatus(StrEnum):
    V1 = "v1"
    ACCEPTED_PENDING = "accepted-pending"


@dataclass(frozen=True, slots=True)
class CatalogDescriptor:
    name: CatalogName
    status: CatalogStatus
    owning_package: str
    resource_vocabulary: tuple[str, ...]
    data_vocabulary: tuple[str, ...]
    expression_methods: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "owningPackage": self.owning_package,
            "resourceVocabulary": list(self.resource_vocabulary),
            "dataVocabulary": list(self.data_vocabulary),
            "expressionMethods": list(self.expression_methods),
        }


@dataclass(frozen=True, slots=True, order=True)
class ResourceReference:
    catalog: CatalogName
    namespace: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog", CatalogName(self.catalog))
        object.__setattr__(self, "namespace", normalize_core_name(self.namespace, "namespace"))
        object.__setattr__(self, "name", normalize_core_name(self.name, "resource name"))

    @property
    def logical_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def canonical(self) -> str:
        return f"{self.catalog.value}:{self.logical_name}"

    def __str__(self) -> str:
        return self.canonical

    def to_dict(self) -> dict[str, str]:
        return {
            "catalog": self.catalog.value,
            "namespace": self.namespace,
            "name": self.name,
        }

    def to_core(self) -> CoreResourceRef:
        return CoreResourceRef(self.catalog.value, self.namespace, self.name)

    @classmethod
    def parse(
        cls,
        value: ResourceReference | CoreResourceRef | str | Mapping[str, object],
        *,
        catalog: CatalogName | str | None = None,
        namespace: str | None = None,
    ) -> ResourceReference:
        if isinstance(value, cls):
            result = value
        elif isinstance(value, CoreResourceRef):
            result = cls(CatalogName(value.catalog), value.namespace, value.name)
        elif isinstance(value, str):
            selected_catalog = str(catalog) if catalog is not None else None
            logical = value
            if ":" in value:
                selected_catalog, logical = value.split(":", 1)
            if selected_catalog is None:
                raise ValueError("unqualified Resource reference requires a Catalog")
            if "." in logical:
                selected_namespace, name = logical.rsplit(".", 1)
            elif namespace is not None:
                selected_namespace, name = namespace, logical
            else:
                raise ValueError("Resource reference must include namespace and name")
            result = cls(CatalogName(selected_catalog), selected_namespace, name)
        elif isinstance(value, Mapping):
            allowed = {"catalog", "namespace", "name"}
            if set(value) - allowed or "name" not in value:
                raise ValueError("Resource reference contains unknown or missing fields")
            mapping_catalog = value.get("catalog", catalog)
            mapping_namespace = value.get("namespace", namespace)
            if mapping_catalog is None or mapping_namespace is None:
                raise ValueError("Resource reference requires Catalog and namespace")
            if not isinstance(mapping_catalog, (str, CatalogName)):
                raise TypeError("Resource reference Catalog must be a string")
            if not isinstance(mapping_namespace, str) or not isinstance(value["name"], str):
                raise TypeError("Resource reference namespace and name must be strings")
            result = cls(CatalogName(mapping_catalog), mapping_namespace, value["name"])
        else:
            raise TypeError("unsupported Resource reference")
        if catalog is not None and result.catalog != CatalogName(catalog):
            raise ValueError("Resource reference Catalog does not match")
        if namespace is not None and result.namespace != normalize_core_name(
            namespace, "namespace"
        ):
            raise ValueError("Resource reference namespace does not match")
        return result


@dataclass(frozen=True, slots=True, order=True)
class SchemaReference:
    catalog: CatalogName
    namespace: str
    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog", CatalogName(self.catalog))
        object.__setattr__(self, "namespace", normalize_core_name(self.namespace, "namespace"))
        object.__setattr__(self, "name", normalize_core_name(self.name, "schema name"))
        if self.version is not None:
            from .canonical import SemanticVersion

            object.__setattr__(self, "version", str(SemanticVersion.parse(self.version)))

    @property
    def logical_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def schema_id(self) -> str:
        return f"{self.catalog.value}:{self.logical_name}"

    @property
    def canonical(self) -> str:
        if self.version is None:
            return self.schema_id
        return f"{self.schema_id}@{self.version}"

    def __str__(self) -> str:
        return self.canonical

    def exact(self) -> SchemaReference:
        if self.version is None:
            raise ValueError("an exact Schema reference requires a version")
        return self

    def to_dict(self) -> dict[str, str]:
        result = {
            "catalog": self.catalog.value,
            "namespace": self.namespace,
            "name": self.name,
        }
        if self.version is not None:
            result["version"] = self.version
        return result

    def to_core(self) -> CoreSchemaRef:
        if self.version is None:
            raise ValueError("Core SchemaRef requires an exact version")
        return CoreSchemaRef(self.catalog.value, self.namespace, self.name, self.version)

    @classmethod
    def parse(
        cls,
        value: SchemaReference | CoreSchemaRef | Mapping[str, object],
        *,
        catalog: CatalogName | str | None = None,
        namespace: str | None = None,
    ) -> SchemaReference:
        if isinstance(value, cls):
            result = value
        elif isinstance(value, CoreSchemaRef):
            result = cls(
                CatalogName(value.catalog),
                value.namespace,
                value.name,
                value.version,
            )
        elif isinstance(value, Mapping):
            allowed = {"catalog", "namespace", "name", "version"}
            if set(value) - allowed or "name" not in value:
                raise ValueError("Schema reference contains unknown or missing fields")
            selected_catalog = value.get("catalog", catalog)
            selected_namespace = value.get("namespace", namespace)
            if selected_catalog is None or selected_namespace is None:
                raise ValueError("Schema reference requires Catalog and namespace")
            selected_version = value.get("version")
            if not isinstance(selected_catalog, (str, CatalogName)):
                raise TypeError("Schema reference Catalog must be a string")
            if not isinstance(selected_namespace, str) or not isinstance(value["name"], str):
                raise TypeError("Schema reference namespace and name must be strings")
            if selected_version is not None and not isinstance(selected_version, str):
                raise TypeError("Schema reference version must be a string")
            result = cls(
                CatalogName(selected_catalog),
                selected_namespace,
                value["name"],
                selected_version,
            )
        else:
            raise TypeError("unsupported Schema reference")
        if catalog is not None and result.catalog != CatalogName(catalog):
            raise ValueError("Schema reference Catalog does not match")
        if namespace is not None and result.namespace != normalize_core_name(
            namespace, "namespace"
        ):
            raise ValueError("Schema reference namespace does not match")
        return result


class CollectionState(StrEnum):
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    PROVISIONING_FAILED = "PROVISIONING_FAILED"
    MIGRATING = "MIGRATING"
    DEPRECATED = "DEPRECATED"


@dataclass(frozen=True, slots=True)
class CollectionDocument:
    ref: ResourceReference
    active_schema_ref: SchemaReference
    semantic_profile: str
    state: CollectionState = CollectionState.PROVISIONING
    labels: Mapping[str, str] = field(default_factory=dict)
    audit_policy_override: Mapping[str, FrozenJson] = field(default_factory=dict)
    lineage_policy_override: Mapping[str, FrozenJson] = field(default_factory=dict)
    cache_policy_override: Mapping[str, FrozenJson] = field(default_factory=dict)
    extensions: Mapping[str, FrozenJson] = field(default_factory=dict)
    format_version: str = "meridian.collection.v1"

    def __post_init__(self) -> None:
        ref = ResourceReference.parse(self.ref, catalog=CatalogName.STRUCTURED)
        schema = SchemaReference.parse(
            self.active_schema_ref,
            catalog=CatalogName.STRUCTURED,
            namespace=ref.namespace,
        ).exact()
        profile = normalize_unicode_name(self.semantic_profile, "semantic profile", maximum=64)
        if profile not in _STRUCTURED_PROFILES:
            raise ValueError("Collection semanticProfile is not a structured V1 profile")
        normalized_labels = {
            normalize_unicode_name(key, "label key", maximum=128): normalize_unicode_name(
                value, "label value", maximum=512
            )
            for key, value in self.labels.items()
        }
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "active_schema_ref", schema)
        object.__setattr__(self, "semantic_profile", profile)
        object.__setattr__(self, "state", CollectionState(self.state))
        object.__setattr__(
            self, "labels", MappingProxyType(dict(sorted(normalized_labels.items())))
        )
        for name in (
            "audit_policy_override",
            "lineage_policy_override",
            "cache_policy_override",
        ):
            object.__setattr__(self, name, _frozen_mapping(getattr(self, name)))
        object.__setattr__(self, "extensions", _extensions(self.extensions))
        if self.format_version != "meridian.collection.v1":
            raise ValueError("unsupported Collection format version")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "id": self.ref.canonical,
            "activeSchemaRef": self.active_schema_ref.to_dict(),
            "semanticProfile": self.semantic_profile,
            "state": self.state.value,
            "labels": dict(self.labels),
            "auditPolicyOverride": _thaw_mapping(self.audit_policy_override),
            "lineagePolicyOverride": _thaw_mapping(self.lineage_policy_override),
            "cachePolicyOverride": _thaw_mapping(self.cache_policy_override),
            "extensions": _thaw_mapping(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class RecordReference:
    collection_ref: ResourceReference
    record_id: FrozenJson

    def __post_init__(self) -> None:
        collection = ResourceReference.parse(self.collection_ref, catalog=CatalogName.STRUCTURED)
        record_id = freeze_json(self.record_id)
        if isinstance(record_id, Mapping) or record_id is None:
            raise ValueError("Record identity must be a scalar or ordered scalar tuple")
        if isinstance(record_id, tuple) and (
            not record_id
            or any(isinstance(item, (tuple, Mapping)) or item is None for item in record_id)
        ):
            raise ValueError("compound Record identity must contain only non-null scalars")
        object.__setattr__(self, "collection_ref", collection)
        object.__setattr__(self, "record_id", record_id)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "collectionRef": self.collection_ref.to_dict(),
            "recordId": thaw_json(self.record_id),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RecordReference:
        if set(value) != {"collectionRef", "recordId"}:
            raise ValueError("RecordRef requires collectionRef and recordId")
        collection = value["collectionRef"]
        if not isinstance(collection, Mapping):
            raise TypeError("RecordRef collectionRef must be an object")
        return cls(ResourceReference.parse(collection), freeze_json(value["recordId"]))


@dataclass(frozen=True, slots=True)
class Record:
    collection_ref: ResourceReference
    record_id: FrozenJson
    values: Mapping[str, FrozenJson]
    record_version: str | int
    created_at: str | datetime
    updated_at: str | datetime
    format_version: str = "meridian.record.v1"

    def __post_init__(self) -> None:
        reference = RecordReference(self.collection_ref, self.record_id)
        if isinstance(self.record_version, bool) or not isinstance(self.record_version, (str, int)):
            raise ValueError("recordVersion must be a string or integer")
        if isinstance(self.record_version, int) and self.record_version < 0:
            raise ValueError("integer recordVersion cannot be negative")
        values = {
            normalize_unicode_name(key, "Record field name"): freeze_json(value)
            for key, value in self.values.items()
        }
        created = normalize_timestamp(self.created_at)
        updated = normalize_timestamp(self.updated_at)
        if updated < created:
            raise ValueError("updatedAt cannot precede createdAt")
        if self.format_version != "meridian.record.v1":
            raise ValueError("unsupported Record format version")
        object.__setattr__(self, "collection_ref", reference.collection_ref)
        object.__setattr__(self, "record_id", reference.record_id)
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(values.items()))))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "collectionRef": self.collection_ref.to_dict(),
            "recordId": thaw_json(self.record_id),
            "recordVersion": self.record_version,
            "values": _thaw_mapping(self.values),
            "createdAt": cast(str, self.created_at),
            "updatedAt": cast(str, self.updated_at),
        }


@dataclass(frozen=True, slots=True)
class ObjectReference:
    resource_ref: ResourceReference
    object_id: str
    digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_ref",
            ResourceReference.parse(self.resource_ref, catalog=CatalogName.OBJECT),
        )
        object.__setattr__(
            self, "object_id", normalize_unicode_name(self.object_id, "Object id", maximum=1024)
        )
        if self.digest is not None and _DIGEST_RE.fullmatch(self.digest) is None:
            raise ValueError("Object digest must be sha256:<lowercase hex>")

    def to_dict(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "resourceRef": self.resource_ref.to_dict(),
            "objectId": self.object_id,
        }
        if self.digest is not None:
            result["digest"] = self.digest
        return result


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    object_ref: ObjectReference
    digest: str
    byte_length: int
    media_type: str
    created_at: str | datetime
    creation_context: Mapping[str, FrozenJson] = field(default_factory=dict)
    user_metadata: Mapping[str, str] = field(default_factory=dict)
    mutability: str = "immutable"
    provenance: Mapping[str, FrozenJson] = field(default_factory=dict)
    format_version: str = "meridian.object.v1"

    def __post_init__(self) -> None:
        reference = ObjectReference(
            self.object_ref.resource_ref,
            self.object_ref.object_id,
            self.object_ref.digest,
        )
        if _DIGEST_RE.fullmatch(self.digest) is None:
            raise ValueError("Object digest must be sha256:<lowercase hex>")
        if reference.digest is not None and reference.digest != self.digest:
            raise ValueError("ObjectRef digest does not match Object metadata")
        if isinstance(self.byte_length, bool) or not isinstance(self.byte_length, int):
            raise TypeError("Object byteLength must be an integer")
        if self.byte_length < 0:
            raise ValueError("Object byteLength cannot be negative")
        media_type = normalize_unicode_name(self.media_type, "media type", maximum=255)
        if "/" not in media_type or any(character.isspace() for character in media_type):
            raise ValueError("mediaType must be an Internet media type")
        if self.mutability not in {"immutable", "mutable"}:
            raise ValueError("Object mutability must be immutable or mutable")
        metadata = {
            normalize_unicode_name(key, "Object metadata key", maximum=128): normalize_unicode_name(
                value, "Object metadata value", maximum=2048
            )
            for key, value in self.user_metadata.items()
        }
        if len(metadata) > 128:
            raise ValueError("Object user metadata may contain at most 128 entries")
        object.__setattr__(self, "object_ref", reference)
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "creation_context", _frozen_mapping(self.creation_context))
        object.__setattr__(self, "user_metadata", MappingProxyType(dict(sorted(metadata.items()))))
        object.__setattr__(self, "provenance", _frozen_mapping(self.provenance))
        if self.format_version != "meridian.object.v1":
            raise ValueError("unsupported Object format version")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "formatVersion": self.format_version,
            "objectRef": self.object_ref.to_dict(),
            "digest": self.digest,
            "byteLength": self.byte_length,
            "mediaType": self.media_type,
            "createdAt": cast(str, self.created_at),
            "creationContext": _thaw_mapping(self.creation_context),
            "userMetadata": dict(self.user_metadata),
            "mutability": self.mutability,
            "provenance": _thaw_mapping(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: FrozenJson
    value: FrozenJson
    serializer_id: str
    schema_fingerprint: str
    created_at: str | datetime
    expires_at: str | datetime | None = None
    source_version: str | int | None = None

    def __post_init__(self) -> None:
        key = freeze_json(self.key)
        if key is None or isinstance(key, Mapping):
            raise ValueError("Cache key must be a non-null scalar or array")
        serializer = normalize_unicode_name(self.serializer_id, "serializer id", maximum=128)
        if _DIGEST_RE.fullmatch(self.schema_fingerprint) is None:
            raise ValueError("schemaFingerprint must be sha256:<lowercase hex>")
        created = normalize_timestamp(self.created_at)
        expires = normalize_timestamp(self.expires_at) if self.expires_at is not None else None
        if expires is not None and expires <= created:
            raise ValueError("Cache expiry must follow creation time")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "value", freeze_json(self.value))
        object.__setattr__(self, "serializer_id", serializer)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "key": thaw_json(self.key),
            "value": thaw_json(self.value),
            "serializerId": self.serializer_id,
            "schemaFingerprint": self.schema_fingerprint,
            "createdAt": cast(str, self.created_at),
            "expiresAt": cast(str | None, self.expires_at),
            "sourceVersion": self.source_version,
        }


def normalize_timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp datetime must be timezone-aware")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("timestamp must be UTC RFC 3339 with six fractional digits")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("timestamp is not a valid UTC instant") from exc
    return value


def validate_base64url(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("bytes wire value must be a base64url string")
    if re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", value) is None:
        raise ValueError("bytes wire value is not base64url")
    try:
        base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    except ValueError as exc:
        raise ValueError("bytes wire value is not base64url") from exc
    return value.rstrip("=")


def _frozen_mapping(value: Mapping[str, object]) -> Mapping[str, FrozenJson]:
    return MappingProxyType({key: freeze_json(item) for key, item in sorted(value.items())})


def _extensions(value: Mapping[str, object]) -> Mapping[str, FrozenJson]:
    return MappingProxyType(
        {normalize_extension_key(key): freeze_json(item) for key, item in sorted(value.items())}
    )


def _thaw_mapping(value: Mapping[str, FrozenJson]) -> dict[str, JsonValue]:
    return {key: thaw_json(item) for key, item in value.items()}


__all__ = [
    "CacheEntry",
    "CatalogDescriptor",
    "CatalogName",
    "CatalogStatus",
    "CollectionDocument",
    "CollectionState",
    "ObjectMetadata",
    "ObjectReference",
    "Record",
    "RecordReference",
    "ResourceReference",
    "SchemaReference",
    "normalize_timestamp",
    "validate_base64url",
]
