# SPDX-License-Identifier: Apache-2.0
"""Portable V1 semantic profiles and query-facing metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from .canonical import FrozenJson, JsonValue, freeze_json, normalize_unicode_name, thaw_json
from .i18n import LocalizedText, normalize_locale
from .resources import ResourceReference

_GEOSPATIAL_OPERATIONS = (
    "distance",
    "distance-within",
    "bounding-box",
    "distance-order",
)


class SemanticKind(StrEnum):
    RELATIONAL = "relational"
    DOCUMENT = "document"
    KEY_VALUE = "key-value"
    SEARCH = "search"
    GEOSPATIAL = "geospatial"
    TIME_SERIES = "time-series"
    RELATION = "relation"
    OBJECT = "object"
    ARTIFACT = "artifact"
    MEDIA = "media"
    CACHE = "cache"


def _field(value: object, name: str) -> str:
    return normalize_unicode_name(value, name)


def _fields(values: Sequence[object], name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    result = tuple(_field(item, name) for item in values)
    if not allow_empty and not result:
        raise ValueError(f"{name} cannot be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique")
    return result


@dataclass(frozen=True, slots=True)
class RelationalProfile:
    alternate_keys: tuple[tuple[str, ...], ...] = ()
    unique_fields: tuple[tuple[str, ...], ...] = ()
    checks: tuple[Mapping[str, FrozenJson], ...] = ()
    kind: SemanticKind = field(default=SemanticKind.RELATIONAL, init=False)

    def __post_init__(self) -> None:
        alternate = tuple(_fields(key, "alternate key") for key in self.alternate_keys)
        unique = tuple(_fields(key, "unique field set") for key in self.unique_fields)
        if len(set(alternate)) != len(alternate) or len(set(unique)) != len(unique):
            raise ValueError("relational key declarations must be unique")
        checks = tuple(
            MappingProxyType({key: freeze_json(item) for key, item in sorted(check.items())})
            for check in self.checks
        )
        object.__setattr__(self, "alternate_keys", alternate)
        object.__setattr__(self, "unique_fields", unique)
        object.__setattr__(self, "checks", checks)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "alternateKeys": [list(item) for item in self.alternate_keys],
            "uniqueFields": [list(item) for item in self.unique_fields],
            "checks": [
                {key: thaw_json(value) for key, value in check.items()} for check in self.checks
            ],
        }


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    body_field: str
    unknown_fields: str = "closed"
    indexed_paths: tuple[str, ...] = ()
    kind: SemanticKind = field(default=SemanticKind.DOCUMENT, init=False)

    def __post_init__(self) -> None:
        if self.unknown_fields not in {"closed", "extensible"}:
            raise ValueError("document unknownFields must be closed or extensible")
        paths = tuple(sorted({_json_pointer(path) for path in self.indexed_paths}))
        if len(paths) != len(self.indexed_paths):
            raise ValueError("document indexedPaths must be unique")
        object.__setattr__(self, "body_field", _field(self.body_field, "document body field"))
        object.__setattr__(self, "indexed_paths", paths)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "bodyField": self.body_field,
            "unknownFields": self.unknown_fields,
            "indexedPaths": list(self.indexed_paths),
        }


@dataclass(frozen=True, slots=True)
class KeyValueProfile:
    key_field: str
    value_field: str
    expires_at_field: str | None = None
    ordered: bool = False
    max_multi_get: int = 1000
    kind: SemanticKind = field(default=SemanticKind.KEY_VALUE, init=False)

    def __post_init__(self) -> None:
        key = _field(self.key_field, "key-value key field")
        value = _field(self.value_field, "key-value value field")
        if key == value:
            raise ValueError("key-value key and value fields must differ")
        expiry = (
            _field(self.expires_at_field, "key-value expiry field")
            if self.expires_at_field is not None
            else None
        )
        if expiry in {key, value}:
            raise ValueError("key-value expiry field must be distinct")
        if isinstance(self.max_multi_get, bool) or not 1 <= self.max_multi_get <= 100_000:
            raise ValueError("maxMultiGet must be between 1 and 100000")
        object.__setattr__(self, "key_field", key)
        object.__setattr__(self, "value_field", value)
        object.__setattr__(self, "expires_at_field", expiry)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "keyField": self.key_field,
            "valueField": self.value_field,
            "expiresAtField": self.expires_at_field,
            "ordered": self.ordered,
            "maxMultiGet": self.max_multi_get,
        }


@dataclass(frozen=True, slots=True)
class FullTextProfile:
    source_fields: tuple[str, ...]
    language_hints: tuple[str, ...] = ("en", "zh")
    analyzer_profile: str = "icu"
    normalized_field: str | None = None
    facets: tuple[str, ...] = ()
    highlights: tuple[str, ...] = ()
    ranking: str = "bm25"
    display_name: LocalizedText | None = None
    kind: SemanticKind = field(default=SemanticKind.SEARCH, init=False)

    def __post_init__(self) -> None:
        sources = _fields(self.source_fields, "full-text source field")
        languages = tuple(sorted({normalize_locale(item) for item in self.language_hints}))
        if not languages:
            raise ValueError("full-text languageHints cannot be empty")
        analyzer = normalize_unicode_name(self.analyzer_profile, "analyzer profile", maximum=128)
        if analyzer != "icu":
            raise ValueError("the portable V1 full-text analyzer profile is icu")
        normalized = (
            _field(self.normalized_field, "normalized text field")
            if self.normalized_field is not None
            else None
        )
        facets = _fields(self.facets, "facet field", allow_empty=True)
        highlights = _fields(self.highlights, "highlight field", allow_empty=True)
        ranking = normalize_unicode_name(self.ranking, "ranking profile", maximum=128)
        object.__setattr__(self, "source_fields", sources)
        object.__setattr__(self, "language_hints", languages)
        object.__setattr__(self, "analyzer_profile", analyzer)
        object.__setattr__(self, "normalized_field", normalized)
        object.__setattr__(self, "facets", facets)
        object.__setattr__(self, "highlights", highlights)
        object.__setattr__(self, "ranking", ranking)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "sourceFields": list(self.source_fields),
            "languageHints": list(self.language_hints),
            "analyzerProfile": self.analyzer_profile,
            "normalizedField": self.normalized_field,
            "facets": list(self.facets),
            "highlights": list(self.highlights),
            "ranking": self.ranking,
            "displayName": None if self.display_name is None else self.display_name.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class GeospatialProfile:
    point_fields: tuple[str, ...]
    distance_unit: str = "m"
    operations: tuple[str, ...] = _GEOSPATIAL_OPERATIONS
    kind: SemanticKind = field(default=SemanticKind.GEOSPATIAL, init=False)

    def __post_init__(self) -> None:
        points = _fields(self.point_fields, "geospatial point field")
        if self.distance_unit != "m":
            raise ValueError("portable V1 public distance unit is meters")
        allowed = {"distance", "distance-within", "bounding-box", "distance-order"}
        operations = tuple(sorted(set(self.operations)))
        if not operations or set(operations) - allowed:
            raise ValueError("unsupported portable geospatial operation")
        object.__setattr__(self, "point_fields", points)
        object.__setattr__(self, "operations", operations)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "pointFields": list(self.point_fields),
            "distanceUnit": self.distance_unit,
            "operations": list(self.operations),
        }


@dataclass(frozen=True, slots=True)
class TimeSeriesProfile:
    timestamp_field: str
    series_identity: tuple[str, ...]
    dimensions: tuple[str, ...]
    measurements: tuple[str, ...]
    exemplar_field: str | None = None
    kind: SemanticKind = field(default=SemanticKind.TIME_SERIES, init=False)

    def __post_init__(self) -> None:
        timestamp = _field(self.timestamp_field, "time-series timestamp field")
        series = _fields(self.series_identity, "time-series identity field")
        dimensions = _fields(self.dimensions, "time-series dimension", allow_empty=True)
        measurements = _fields(self.measurements, "time-series measurement")
        all_fields = (timestamp, *series, *dimensions, *measurements)
        if len(set(all_fields)) != len(all_fields):
            raise ValueError("time-series field roles must be disjoint")
        exemplar = (
            _field(self.exemplar_field, "time-series exemplar field")
            if self.exemplar_field is not None
            else None
        )
        if exemplar in all_fields:
            raise ValueError("time-series exemplar field must be distinct")
        object.__setattr__(self, "timestamp_field", timestamp)
        object.__setattr__(self, "series_identity", series)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "measurements", measurements)
        object.__setattr__(self, "exemplar_field", exemplar)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "timestampField": self.timestamp_field,
            "seriesIdentity": list(self.series_identity),
            "dimensions": list(self.dimensions),
            "measurements": list(self.measurements),
            "exemplarField": self.exemplar_field,
        }


@dataclass(frozen=True, slots=True)
class RelationProfile:
    source_field: str
    target_field: str
    directed: bool
    source_collections: tuple[ResourceReference, ...]
    target_collections: tuple[ResourceReference, ...]
    kind: SemanticKind = field(default=SemanticKind.RELATION, init=False)

    def __post_init__(self) -> None:
        source_field = _field(self.source_field, "Relation source field")
        target_field = _field(self.target_field, "Relation target field")
        if source_field == target_field:
            raise ValueError("Relation source and target fields must differ")
        sources = tuple(
            sorted(
                ResourceReference.parse(item, catalog="structured")
                for item in self.source_collections
            )
        )
        targets = tuple(
            sorted(
                ResourceReference.parse(item, catalog="structured")
                for item in self.target_collections
            )
        )
        if not sources or not targets:
            raise ValueError("Relation endpoints require allowed Collections")
        if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
            raise ValueError("Relation endpoint Collections must be unique")
        object.__setattr__(self, "source_field", source_field)
        object.__setattr__(self, "target_field", target_field)
        object.__setattr__(self, "source_collections", sources)
        object.__setattr__(self, "target_collections", targets)

    def neighbors(self, collection: ResourceReference) -> tuple[ResourceReference, ...]:
        ref = ResourceReference.parse(collection)
        result: set[ResourceReference] = set()
        if ref in self.source_collections:
            result.update(self.target_collections)
        if not self.directed and ref in self.target_collections:
            result.update(self.source_collections)
        return tuple(sorted(result))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "sourceField": self.source_field,
            "targetField": self.target_field,
            "directed": self.directed,
            "sourceCollections": [item.to_dict() for item in self.source_collections],
            "targetCollections": [item.to_dict() for item in self.target_collections],
        }


@dataclass(frozen=True, slots=True)
class ObjectProfile:
    profile: str = "object"
    mutability: str = "immutable"
    range_reads: bool = True
    conditional_create: bool = True
    bounded_prefix_list: bool = True
    metadata: Mapping[str, FrozenJson] = field(default_factory=dict)
    kind: SemanticKind = field(default=SemanticKind.OBJECT, init=False)

    def __post_init__(self) -> None:
        if self.profile not in {"object", "artifact", "media"}:
            raise ValueError("Object profile must be object, artifact, or media")
        if self.mutability not in {"immutable", "mutable"}:
            raise ValueError("Object mutability must be immutable or mutable")
        if self.profile == "artifact" and self.mutability != "immutable":
            raise ValueError("artifact profile is immutable and publish-once")
        metadata = MappingProxyType(
            {key: freeze_json(item) for key, item in sorted(self.metadata.items())}
        )
        object.__setattr__(self, "kind", SemanticKind(self.profile))
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "profile": self.profile,
            "mutability": self.mutability,
            "rangeReads": self.range_reads,
            "conditionalCreate": self.conditional_create,
            "boundedPrefixList": self.bounded_prefix_list,
            "metadata": {key: thaw_json(item) for key, item in self.metadata.items()},
        }


@dataclass(frozen=True, slots=True)
class CacheProfile:
    consistency: str = "eventual"
    authoritative: bool = False
    kind: SemanticKind = field(default=SemanticKind.CACHE, init=False)

    def __post_init__(self) -> None:
        if self.consistency not in {"eventual", "session", "strong"}:
            raise ValueError("unsupported cache consistency profile")
        if self.authoritative:
            raise ValueError("Meridian cache state is never authoritative")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "consistency": self.consistency,
            "authoritative": False,
        }


type ProfileDefinition = (
    RelationalProfile
    | DocumentProfile
    | KeyValueProfile
    | FullTextProfile
    | GeospatialProfile
    | TimeSeriesProfile
    | RelationProfile
    | ObjectProfile
    | CacheProfile
)


def profile_from_mapping(value: Mapping[str, object]) -> ProfileDefinition:
    if "kind" not in value:
        raise ValueError("semantic profile requires kind")
    kind = SemanticKind(cast(str, value["kind"]))
    item = dict(value)
    item.pop("kind")
    result: ProfileDefinition
    if kind is SemanticKind.RELATIONAL:
        result = RelationalProfile(
            alternate_keys=_tuples(item.pop("alternateKeys", ())),
            unique_fields=_tuples(item.pop("uniqueFields", ())),
            checks=tuple(cast(Sequence[Mapping[str, FrozenJson]], item.pop("checks", ()))),
        )
    elif kind is SemanticKind.DOCUMENT:
        result = DocumentProfile(
            body_field=cast(str, item.pop("bodyField")),
            unknown_fields=cast(str, item.pop("unknownFields", "closed")),
            indexed_paths=tuple(cast(Sequence[str], item.pop("indexedPaths", ()))),
        )
    elif kind is SemanticKind.KEY_VALUE:
        result = KeyValueProfile(
            key_field=cast(str, item.pop("keyField")),
            value_field=cast(str, item.pop("valueField")),
            expires_at_field=cast(str | None, item.pop("expiresAtField", None)),
            ordered=cast(bool, item.pop("ordered", False)),
            max_multi_get=cast(int, item.pop("maxMultiGet", 1000)),
        )
    elif kind is SemanticKind.SEARCH:
        display = item.pop("displayName", None)
        result = FullTextProfile(
            source_fields=tuple(cast(Sequence[str], item.pop("sourceFields"))),
            language_hints=tuple(cast(Sequence[str], item.pop("languageHints", ("en", "zh")))),
            analyzer_profile=cast(str, item.pop("analyzerProfile", "icu")),
            normalized_field=cast(str | None, item.pop("normalizedField", None)),
            facets=tuple(cast(Sequence[str], item.pop("facets", ()))),
            highlights=tuple(cast(Sequence[str], item.pop("highlights", ()))),
            ranking=cast(str, item.pop("ranking", "bm25")),
            display_name=None if display is None else LocalizedText.from_mapping(display),
        )
    elif kind is SemanticKind.GEOSPATIAL:
        result = GeospatialProfile(
            point_fields=tuple(cast(Sequence[str], item.pop("pointFields"))),
            distance_unit=cast(str, item.pop("distanceUnit", "m")),
            operations=tuple(cast(Sequence[str], item.pop("operations", _GEOSPATIAL_OPERATIONS))),
        )
    elif kind is SemanticKind.TIME_SERIES:
        result = TimeSeriesProfile(
            timestamp_field=cast(str, item.pop("timestampField")),
            series_identity=tuple(cast(Sequence[str], item.pop("seriesIdentity"))),
            dimensions=tuple(cast(Sequence[str], item.pop("dimensions", ()))),
            measurements=tuple(cast(Sequence[str], item.pop("measurements"))),
            exemplar_field=cast(str | None, item.pop("exemplarField", None)),
        )
    elif kind is SemanticKind.RELATION:
        result = RelationProfile(
            source_field=cast(str, item.pop("sourceField")),
            target_field=cast(str, item.pop("targetField")),
            directed=cast(bool, item.pop("directed")),
            source_collections=tuple(
                ResourceReference.parse(cast(Mapping[str, object], entry))
                for entry in cast(Sequence[object], item.pop("sourceCollections"))
            ),
            target_collections=tuple(
                ResourceReference.parse(cast(Mapping[str, object], entry))
                for entry in cast(Sequence[object], item.pop("targetCollections"))
            ),
        )
    elif kind in {SemanticKind.OBJECT, SemanticKind.ARTIFACT, SemanticKind.MEDIA}:
        result = ObjectProfile(
            profile=cast(str, item.pop("profile", kind.value)),
            mutability=cast(str, item.pop("mutability", "immutable")),
            range_reads=cast(bool, item.pop("rangeReads", True)),
            conditional_create=cast(bool, item.pop("conditionalCreate", True)),
            bounded_prefix_list=cast(bool, item.pop("boundedPrefixList", True)),
            metadata=cast(Mapping[str, FrozenJson], item.pop("metadata", {})),
        )
    elif kind is SemanticKind.CACHE:
        result = CacheProfile(
            consistency=cast(str, item.pop("consistency", "eventual")),
            authoritative=cast(bool, item.pop("authoritative", False)),
        )
    else:
        raise ValueError(f"unsupported semantic profile: {kind.value}")
    if item:
        raise ValueError(f"unknown semantic profile fields: {sorted(item)!r}")
    return result


def _tuples(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("key declarations must be arrays")
    return tuple(tuple(cast(Sequence[str], item)) for item in value)


def _json_pointer(value: object) -> str:
    pointer = normalize_unicode_name(value, "JSON pointer", maximum=1024)
    if pointer and not pointer.startswith("/"):
        raise ValueError("indexed document paths must be JSON Pointers")
    for segment in pointer.split("/")[1:]:
        if re_search_invalid_pointer(segment):
            raise ValueError("invalid JSON Pointer escape")
    return pointer


def re_search_invalid_pointer(segment: str) -> bool:
    index = 0
    while index < len(segment):
        if segment[index] == "~":
            if index + 1 >= len(segment) or segment[index + 1] not in {"0", "1"}:
                return True
            index += 2
        else:
            index += 1
    return False


__all__ = [
    "CacheProfile",
    "DocumentProfile",
    "FullTextProfile",
    "GeospatialProfile",
    "KeyValueProfile",
    "ObjectProfile",
    "ProfileDefinition",
    "RelationProfile",
    "RelationalProfile",
    "SemanticKind",
    "TimeSeriesProfile",
    "profile_from_mapping",
]
