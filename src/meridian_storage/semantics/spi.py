# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral semantics SPI consumed by conforming Adapter packages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .canonical import FrozenJson, JsonValue, freeze_json, sha256_fingerprint, thaw_json
from .resources import CollectionDocument, ResourceReference, SchemaReference
from .schemas import SchemaDocument


@dataclass(frozen=True, slots=True)
class ActivationPlan:
    resource_ref: ResourceReference
    target_schema_ref: SchemaReference
    current_schema_ref: SchemaReference | None = None
    steps: tuple[Mapping[str, FrozenJson], ...] = ()
    requirements: tuple[str, ...] = ()
    metadata: Mapping[str, FrozenJson] = field(default_factory=dict)

    def __post_init__(self) -> None:
        resource = ResourceReference.parse(self.resource_ref)
        target = SchemaReference.parse(
            self.target_schema_ref,
            catalog=resource.catalog,
            namespace=resource.namespace,
        ).exact()
        current = (
            None
            if self.current_schema_ref is None
            else SchemaReference.parse(
                self.current_schema_ref,
                catalog=resource.catalog,
                namespace=resource.namespace,
            ).exact()
        )
        steps = tuple(
            MappingProxyType({key: freeze_json(item) for key, item in sorted(step.items())})
            for step in self.steps
        )
        requirements = tuple(sorted(set(self.requirements)))
        if len(requirements) != len(self.requirements):
            raise ValueError("Activation requirements must be unique")
        metadata = MappingProxyType(
            {key: freeze_json(item) for key, item in sorted(self.metadata.items())}
        )
        object.__setattr__(self, "resource_ref", resource)
        object.__setattr__(self, "target_schema_ref", target)
        object.__setattr__(self, "current_schema_ref", current)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "metadata", metadata)

    @property
    def fingerprint(self) -> str:
        return sha256_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "resourceRef": self.resource_ref.to_dict(),
            "targetSchemaRef": self.target_schema_ref.to_dict(),
            "currentSchemaRef": (
                None if self.current_schema_ref is None else self.current_schema_ref.to_dict()
            ),
            "steps": [{key: thaw_json(item) for key, item in step.items()} for step in self.steps],
            "requirements": list(self.requirements),
            "metadata": {key: thaw_json(item) for key, item in self.metadata.items()},
        }


@dataclass(frozen=True, slots=True)
class ActivationResult:
    plan_fingerprint: str
    physical_fingerprint: str
    registry_revision: str
    provenance: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class SemanticsAdapter(Protocol):
    """Exact V1 logical-schema SPI; no Engine selection or credentials cross it."""

    def validate_definition(
        self,
        schema: SchemaDocument,
        resource: CollectionDocument | None = None,
    ) -> None: ...

    def plan_activation(
        self,
        resource: CollectionDocument,
        current_schema: SchemaDocument | None,
        target_schema: SchemaDocument,
    ) -> ActivationPlan: ...

    def apply_activation(self, plan: ActivationPlan) -> ActivationResult: ...

    def read_registry_revision(self) -> str: ...

    def encode_value(
        self,
        schema: SchemaDocument,
        value: Mapping[str, FrozenJson],
    ) -> object: ...

    def decode_value(
        self,
        schema: SchemaDocument,
        value: object,
    ) -> Mapping[str, FrozenJson]: ...

    def export_logical(
        self,
        resources: Sequence[ResourceReference],
    ) -> Mapping[str, JsonValue]: ...

    def import_logical(self, payload: Mapping[str, object]) -> None: ...


__all__ = ["ActivationPlan", "ActivationResult", "SemanticsAdapter"]
