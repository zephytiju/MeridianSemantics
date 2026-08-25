# SPDX-License-Identifier: Apache-2.0
"""Deterministic JSON and identifier helpers for Meridian semantics."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import total_ordering
from types import MappingProxyType
from typing import cast

type JsonValue = bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None
type FrozenJson = (
    bool | int | float | str | tuple[FrozenJson, ...] | Mapping[str, FrozenJson] | None
)

_CORE_NAME_RE = re.compile(r"^[A-Za-z](?:[A-Za-z0-9_.-]{0,253}[A-Za-z0-9])?$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def freeze_json(value: object) -> FrozenJson:
    """Return an immutable JSON value and reject ambiguous input."""

    return cast(FrozenJson, _freeze(value, seen=set()))


def _freeze(value: object, *, seen: set[int]) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        if any(unicodedata.category(character) == "Cs" for character in value):
            raise ValueError("surrogate code points are not valid Meridian UTF-8 JSON")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not valid Meridian JSON")
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise ValueError("cyclic mappings are not valid Meridian JSON")
        seen.add(marker)
        mapping_result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Meridian JSON object keys must be strings")
            mapping_result[key] = _freeze(item, seen=seen)
        seen.remove(marker)
        return MappingProxyType(mapping_result)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        marker = id(value)
        if marker in seen:
            raise ValueError("cyclic sequences are not valid Meridian JSON")
        seen.add(marker)
        sequence_result = tuple(_freeze(item, seen=seen) for item in value)
        seen.remove(marker)
        return sequence_result
    raise TypeError(f"unsupported Meridian JSON value: {type(value).__name__}")


def thaw_json(value: FrozenJson) -> JsonValue:
    """Return ordinary dictionaries and lists suitable for JSON encoding."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return cast(JsonValue, value)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one value as canonical UTF-8 JSON with lexical object keys."""

    frozen = freeze_json(value)
    return json.dumps(
        thaw_json(frozen),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_fingerprint(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def document_fingerprint(value: Mapping[str, object]) -> str:
    """Fingerprint a canonical document without its derived fingerprint field."""

    return sha256_fingerprint({key: item for key, item in value.items() if key != "fingerprint"})


def normalize_unicode_name(value: object, field: str, *, maximum: int = 255) -> str:
    """Normalize a consumer-visible Unicode identifier to NFC."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized.encode("utf-8")) > maximum:
        raise ValueError(f"{field} exceeds {maximum} UTF-8 bytes")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized):
        raise ValueError(f"{field} contains a control or surrogate character")
    return normalized


def normalize_core_name(value: object, field: str) -> str:
    """Apply the exact public logical-name grammar released by Core 1.0.0."""

    normalized = normalize_unicode_name(value, field)
    if _CORE_NAME_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field} is not a Core 1.0.0 logical-name segment")
    return normalized


def normalize_extension_key(value: object) -> str:
    key = normalize_unicode_name(value, "extension key", maximum=512)
    is_uri = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$", key) is not None
    is_reverse_domain = (
        re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/[A-Za-z0-9._/-]+$", key) is not None
        and "." in key.split("/", 1)[0]
    )
    if not (is_uri or is_reverse_domain):
        raise ValueError("extension keys must be namespaced URIs or reverse-domain identifiers")
    return key


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class SemanticVersion:
    """SemVer 2.0 precedence used for immutable Schema versions."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: object) -> SemanticVersion:
        if not isinstance(value, str):
            raise TypeError("version must be a semantic-version string")
        match = _SEMVER_RE.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid semantic version: {value!r}")
        prerelease = tuple((match.group("pre") or "").split(".")) if match.group("pre") else ()
        build = tuple((match.group("build") or "").split(".")) if match.group("build") else ()
        if any(not part for part in (*prerelease, *build)):
            raise ValueError("semantic-version identifiers cannot be empty")
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
            raise ValueError("numeric prerelease identifiers cannot contain leading zeroes")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease,
            build,
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        release = (self.major, self.minor, self.patch)
        other_release = (other.major, other.minor, other.patch)
        if release != other_release:
            return release < other_release
        return _prerelease_less(self.prerelease, other.prerelease)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))


def _prerelease_less(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if left == right:
        return False
    if not left:
        return False
    if not right:
        return True
    for left_item, right_item in zip(left, right, strict=False):
        if left_item == right_item:
            continue
        if left_item.isdigit() and right_item.isdigit():
            return int(left_item) < int(right_item)
        if left_item.isdigit() != right_item.isdigit():
            return left_item.isdigit()
        return left_item < right_item
    return len(left) < len(right)


def sorted_json_mapping(value: Mapping[str, object]) -> Mapping[str, FrozenJson]:
    result: dict[str, FrozenJson] = {}
    for key in sorted(value):
        result[normalize_unicode_name(key, "metadata key", maximum=512)] = freeze_json(value[key])
    return MappingProxyType(result)


__all__ = [
    "FrozenJson",
    "JsonValue",
    "SemanticVersion",
    "canonical_json_bytes",
    "document_fingerprint",
    "freeze_json",
    "normalize_core_name",
    "normalize_extension_key",
    "normalize_unicode_name",
    "sha256_fingerprint",
    "sorted_json_mapping",
    "thaw_json",
]
