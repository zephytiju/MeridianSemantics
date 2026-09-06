# SPDX-License-Identifier: Apache-2.0
"""Portable put v2 wire fixtures; engine existence cases are consumed by adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from hypothesis import given
from hypothesis import strategies as st

from meridian_storage import Expression, Operation
from meridian_storage.semantics import (
    CacheCatalogProvider,
    InvalidDefinition,
    StructuredCatalogProvider,
    cache_manifest,
    structured_manifest,
)

ROOT = Path(__file__).parents[2]
FIXTURES = json.loads((ROOT / "contracts/conformance/structured-put.v2.json").read_text())
SCHEMA = json.loads(
    (ROOT / "contracts/operations/meridian.structured.put.v2.schema.json").read_text()
)
PROVIDER = StructuredCatalogProvider()


@pytest.mark.parametrize("fixture", FIXTURES["valid"], ids=lambda row: row["name"])
def test_wire_fixtures_and_independent_canonical_fingerprints(fixture: dict) -> None:
    expression = Expression.from_mapping(fixture["expression"])
    jsonschema.Draft202012Validator(SCHEMA).validate(expression.to_dict()["arguments"])
    operation = PROVIDER.normalize(expression)
    assert operation.to_dict() == fixture["operation"]
    assert Operation.from_mapping(fixture["operation"]).to_dict() == operation.to_dict()
    assert operation.request_fingerprint == fixture["requestFingerprint"]
    canonical = json.dumps(
        fixture["operation"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert "sha256:" + hashlib.sha256(canonical).hexdigest() == operation.request_fingerprint
    assert operation.requirements[0].operation_version == "2.0.0"
    assert operation.input["data"] == expression.arguments["data"]
    assert operation.idempotent and not operation.read_only


@pytest.mark.parametrize("fixture", FIXTURES["invalid"], ids=lambda row: row["name"])
def test_invalid_and_legacy_wire_input_fails_closed(fixture: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(fixture["expression"]["arguments"])
    with pytest.raises(InvalidDefinition) as raised:
        PROVIDER.normalize(Expression.from_mapping(fixture["expression"]))
    assert raised.value.code == fixture["errorCode"]
    assert raised.value.requirement == fixture["requirement"]


@pytest.mark.parametrize("mode", ["", None, False, 0, [], {}, "replace", "UPDATE"])
def test_public_surface_rejects_invalid_modes(mode: object) -> None:
    with pytest.raises(InvalidDefinition):
        PROVIDER.create_surface().put(resource="example.customers", data={}, mode=mode)  # type: ignore[arg-type]


@pytest.mark.parametrize("version", [0, 1, "v1", False, {}, []])
def test_default_and_explicit_create_reject_any_non_null_cas(version: object) -> None:
    surface = PROVIDER.create_surface()
    with pytest.raises(InvalidDefinition):
        surface.put(resource="example.customers", data={}, expected_version=version)  # type: ignore[arg-type]
    with pytest.raises(InvalidDefinition):
        surface.put(
            resource="example.customers",
            data={},
            mode="if_absent",
            expected_version=version,  # type: ignore[arg-type]
        )


def test_default_is_serialized_and_old_omission_never_defaults() -> None:
    surface = PROVIDER.create_surface()
    default = surface.put(resource="example.customers", data={"id": "1"})
    explicit = surface.put(resource="example.customers", data={"id": "1"}, mode="if_absent")
    assert default.to_dict() == explicit.to_dict()
    assert default.arguments["mode"] == "if_absent"
    assert PROVIDER.normalize(default) == PROVIDER.normalize(explicit)
    legacy = default.to_dict()
    del legacy["arguments"]["mode"]
    with pytest.raises(InvalidDefinition, match="missing"):
        PROVIDER.normalize(Expression.from_mapping(legacy))


@given(st.dictionaries(st.text(alphabet="abc", min_size=1), st.integers(), max_size=5))
def test_mode_and_cas_affect_fingerprint_but_mapping_order_and_null_do_not(data: dict) -> None:
    surface = PROVIDER.create_surface()
    fingerprints = set()
    for mode in ("if_absent", "update", "upsert"):
        expression = surface.put(resource="example.customers", data=data, mode=mode)
        operation = PROVIDER.normalize(expression)
        reordered = Expression(
            "structured",
            "put",
            {
                "mode": mode,
                "data": dict(reversed(list(data.items()))),
                "resource": "example.customers",
                "expectedVersion": None,
            },
        )
        assert operation.request_fingerprint == PROVIDER.normalize(reordered).request_fingerprint
        fingerprints.add(operation.request_fingerprint)
        if mode != "if_absent":
            for version in (0, 1):
                cas = surface.put(
                    resource="example.customers", data=data, mode=mode, expected_version=version
                )
                fingerprints.add(PROVIDER.normalize(cas).request_fingerprint)
    assert len(fingerprints) == 7


def test_version_boundary_preserves_other_operation_contracts() -> None:
    manifest = structured_manifest()
    assert manifest.catalog_contract_version == "2.0.0"
    assert manifest.operation_for("put").operation_version == "2.0.0"
    assert all(op.operation_version == "1.0.0" for op in manifest.operations if op.method != "put")
    assert cache_manifest().catalog_contract_version == "1.0.0"
    assert all(op.operation_version == "1.0.0" for op in cache_manifest().operations)
    provider = CacheCatalogProvider()
    operation = provider.normalize(
        provider.create_surface().put(resource="example.cache", key=1, value=2)
    )
    assert "mode" not in operation.input


def test_portable_existence_matrix_is_complete_and_normalizes() -> None:
    cases = FIXTURES["existenceCases"]
    expected = {
        (mode, present, version)
        for mode in ("if_absent", "update", "upsert")
        for present in (False, True)
        for version in ([None] if mode == "if_absent" else [None, 0, 1, 2])
    }
    assert {(c["mode"], c["recordPresent"], c["expectedVersion"]) for c in cases} == expected
    assert len(cases) == len(expected)
    for case in cases:
        expression = PROVIDER.create_surface().put(
            resource="example.customers",
            data={"id": "1"},
            mode=case["mode"],
            expected_version=case["expectedVersion"],
        )
        assert PROVIDER.normalize(expression).input["mode"] == case["mode"]
        assert case["expectedOutcome"] in {"create", "update", "conflict"}
        if case["expectedOutcome"] == "conflict":
            assert case["errorType"] == "ConflictError"


@pytest.mark.parametrize("mode", ["if_absent", "update", "upsert"])
def test_released_core_requires_v2_capability_for_every_mode(mode: str) -> None:
    from meridian_storage.spi import (
        AdapterDescriptor,
        CapabilityManifest,
        OperationCapability,
        capability_violations,
    )

    operation = PROVIDER.normalize(
        PROVIDER.create_surface().put(resource="example.customers", data={"id": "1"}, mode=mode)
    )
    for version in ("1.0.0", "2.0.0"):
        descriptor = AdapterDescriptor(
            adapter_id="conformance",
            adapter_contract_version="1.0.0",
            driver="fixture",
            supported_engine_versions={"fixture": ("1",)},
            capabilities=(OperationCapability("meridian.structured.put", (version,)),),
        )
        manifest = CapabilityManifest(descriptor, "fixture", "1")
        violations = capability_violations(manifest, operation.requirements)
        if version == "1.0.0":
            assert len(violations) == 1
            assert violations[0].reason == "Operation version is not advertised"
        else:
            assert violations == ()
