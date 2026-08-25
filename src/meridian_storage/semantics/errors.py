# SPDX-License-Identifier: Apache-2.0
"""Stable public failures for Meridian logical semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from meridian_storage.errors import (
    CompatibilityError,
    ConflictError,
    ConstraintError,
    NotFoundError,
    UnavailableError,
    ValidationError,
)


class _SemanticsDetails:
    requirement: str | None
    logical_references: tuple[str, ...]

    def _set_semantics_details(
        self,
        *,
        requirement: str | None,
        logical_references: tuple[str, ...],
    ) -> None:
        self.requirement = requirement
        self.logical_references = tuple(sorted(set(logical_references)))

    def to_dict(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], super().to_dict())  # type: ignore[misc]
        if self.requirement is not None:
            payload["requirement"] = self.requirement
        if self.logical_references:
            payload["logicalReferences"] = list(self.logical_references)
        return payload


def _details(
    details: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None, tuple[str, ...]]:
    copied = dict(details)
    requirement = copied.pop("requirement", None)
    references = copied.pop("logical_references", ())
    if requirement is not None and not isinstance(requirement, str):
        raise TypeError("requirement must be a string")
    if not isinstance(references, tuple) or not all(isinstance(item, str) for item in references):
        raise TypeError("logical_references must be a tuple of strings")
    return copied, requirement, references


class InvalidDefinition(_SemanticsDetails, ValidationError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_INVALID_DEFINITION", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class ResourceAlreadyExists(_SemanticsDetails, ConflictError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_RESOURCE_EXISTS", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class ResourceNotFound(_SemanticsDetails, NotFoundError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_RESOURCE_NOT_FOUND", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class SchemaVersionConflict(_SemanticsDetails, ConflictError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_SCHEMA_VERSION_CONFLICT", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class IncompatibleSchema(_SemanticsDetails, CompatibilityError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_INCOMPATIBLE_SCHEMA", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class InvalidRelationProfile(_SemanticsDetails, ValidationError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_INVALID_RELATION", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class UnsupportedSemantic(_SemanticsDetails, ValidationError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_UNSUPPORTED", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class CapabilityMismatch(_SemanticsDetails, CompatibilityError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_CAPABILITY_MISMATCH", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class ActivationFailed(_SemanticsDetails, UnavailableError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        core.setdefault("retryable", True)
        super().__init__("MERIDIAN_SEMANTICS_ACTIVATION_FAILED", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class RecordValidationFailed(_SemanticsDetails, ValidationError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_RECORD_INVALID", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class ReferenceViolation(_SemanticsDetails, ConstraintError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_REFERENCE_VIOLATION", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


class RegistryRevisionConflict(_SemanticsDetails, ConflictError):
    def __init__(self, message: str, **details: Any) -> None:
        core, requirement, references = _details(details)
        super().__init__("MERIDIAN_SEMANTICS_REGISTRY_REVISION", message, **core)
        self._set_semantics_details(requirement=requirement, logical_references=references)


__all__ = [
    "ActivationFailed",
    "CapabilityMismatch",
    "IncompatibleSchema",
    "InvalidDefinition",
    "InvalidRelationProfile",
    "RecordValidationFailed",
    "ReferenceViolation",
    "RegistryRevisionConflict",
    "ResourceAlreadyExists",
    "ResourceNotFound",
    "SchemaVersionConflict",
    "UnsupportedSemantic",
]
