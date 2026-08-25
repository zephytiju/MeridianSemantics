#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify checked-in V1 contracts against the Python implementation."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import jsonschema

import meridian_storage.semantics as semantics
from meridian_storage.semantics import (
    ActivationFailed,
    CapabilityMismatch,
    IncompatibleSchema,
    InMemoryMetadataRepository,
    InvalidDefinition,
    InvalidRelationProfile,
    RecordValidationFailed,
    ReferenceViolation,
    RegistryRevisionConflict,
    ResourceAlreadyExists,
    ResourceNotFound,
    SchemaAPI,
    SchemaDocument,
    SchemaVersionConflict,
    UnsupportedSemantic,
    cache_manifest,
    catalog_registry,
    structured_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    public = load(ROOT / "contracts/public-api/meridian-semantics.v1.json")
    assert public["version"] == semantics.__version__
    assert public["core"] == "1.0.0"
    assert public["exports"] == sorted(semantics.__all__)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == ["meridian-storage-core==1.0.0"]

    manifests = {"structured": structured_manifest(), "cache": cache_manifest()}
    public_catalogs = public["catalogs"]
    assert isinstance(public_catalogs, dict)
    for catalog, manifest in manifests.items():
        assert (
            sorted(contract.method for contract in manifest.operations) == public_catalogs[catalog]
        )

    registry_contract = load(ROOT / "contracts/catalogs/meridian-catalog-registry.v1.json")
    registry = catalog_registry()
    assert list(registry) == ["structured", "object", "cache", "evidence", "streaming"]
    catalog_rows = registry_contract["catalogs"]
    assert isinstance(catalog_rows, list)
    assert [row["name"] for row in catalog_rows] == list(registry)  # type: ignore[index]
    not_catalogs = registry_contract["notCatalogs"]
    assert isinstance(not_catalogs, list)
    assert not set(not_catalogs) & set(registry)

    for path in sorted((ROOT / "contracts/logical-schema").glob("*.schema.json")):
        jsonschema.Draft202012Validator.check_schema(load(path))
    schema_contract = load(ROOT / "contracts/logical-schema/meridian.schema.v1.schema.json")
    golden = load(ROOT / "contracts/conformance/valid/customer.schema.json")
    jsonschema.Draft202012Validator(schema_contract).validate(golden)
    document = SchemaDocument.from_definition(
        catalog="structured",
        namespace="example",
        name="customer",
        version="1.0.0",
        definition=golden,
    )
    assert document.to_dict() == golden

    invalid = load(ROOT / "contracts/conformance/invalid/unknown-field.schema.json")
    api = SchemaAPI(InMemoryMetadataRepository())
    try:
        api.publish(
            catalog="structured",
            namespace="example",
            name="invalid",
            version="1.0.0",
            definition=invalid["definition"],  # type: ignore[arg-type]
        )
    except InvalidDefinition as error:
        assert error.requirement == invalid["expectedRequirement"]
    else:
        raise AssertionError("invalid conformance fixture was accepted")

    error_types = (
        ActivationFailed,
        CapabilityMismatch,
        IncompatibleSchema,
        InvalidDefinition,
        InvalidRelationProfile,
        RecordValidationFailed,
        ReferenceViolation,
        RegistryRevisionConflict,
        ResourceAlreadyExists,
        ResourceNotFound,
        SchemaVersionConflict,
        UnsupportedSemantic,
    )
    error_codes = sorted(error_type("contract probe").code for error_type in error_types)
    assert error_codes == public["errorCodes"]
    print(
        json.dumps(
            {
                "catalogs": list(registry),
                "errors": error_codes,
                "goldenFingerprint": document.fingerprint,
                "schemas": 5,
                "version": semantics.__version__,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
