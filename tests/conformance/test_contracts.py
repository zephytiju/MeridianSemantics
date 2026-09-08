# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import jsonschema
import pytest

import meridian_storage.semantics as semantics
from meridian_storage.semantics import (
    CacheEntry,
    CatalogName,
    CollectionDocument,
    ObjectMetadata,
    ObjectReference,
    Record,
    RelationProfile,
    ResourceReference,
    SchemaAPI,
    SchemaDocument,
    catalog_registry,
)
from tests.support import DIGEST, FINGERPRINT, NOW, field, schema

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "contracts"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.conformance
def test_every_json_schema_is_a_valid_draft_2020_12_schema() -> None:
    paths = sorted((CONTRACTS / "logical-schema").glob("*.schema.json"))
    assert len(paths) == 5
    for path in paths:
        jsonschema.Draft202012Validator.check_schema(_json(path))


@pytest.mark.conformance
def test_canonical_schema_golden_fixture_round_trips() -> None:
    payload = _json(CONTRACTS / "conformance" / "valid" / "customer.schema.json")
    validator = jsonschema.Draft202012Validator(
        _json(CONTRACTS / "logical-schema" / "meridian.schema.v1.schema.json")
    )
    validator.validate(payload)
    document = SchemaDocument.from_definition(
        catalog="structured",
        namespace="example",
        name="customer",
        version="1.0.0",
        definition=payload,
    )
    assert document.to_dict() == payload


@pytest.mark.conformance
def test_invalid_publication_fixture_is_deterministically_rejected() -> None:
    fixture = _json(CONTRACTS / "conformance" / "invalid" / "unknown-field.schema.json")
    publication = fixture["publication"]
    assert isinstance(publication, dict)
    api = SchemaAPI(semantics.InMemoryMetadataRepository(clock=lambda: NOW))
    with pytest.raises(semantics.InvalidDefinition) as raised:
        api.publish(
            catalog=publication["catalog"],  # type: ignore[arg-type]
            namespace=publication["namespace"],  # type: ignore[arg-type]
            name=publication["name"],  # type: ignore[arg-type]
            version=publication["version"],  # type: ignore[arg-type]
            definition=fixture["definition"],  # type: ignore[arg-type]
        )
    assert raised.value.requirement == fixture["expectedRequirement"]


@pytest.mark.conformance
def test_resource_wire_documents_validate_against_contracts() -> None:
    structured = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    object_resource = ResourceReference(CatalogName.OBJECT, "example", "objects")
    documents = {
        "meridian.collection.v1.schema.json": CollectionDocument(
            structured, schema().ref, "relational"
        ).to_dict(),
        "meridian.record.v1.schema.json": Record(
            structured, "1", {"name": "Ada"}, 1, NOW, NOW
        ).to_dict(),
        "meridian.object.v1.schema.json": ObjectMetadata(
            ObjectReference(object_resource, "file", DIGEST),
            DIGEST,
            1,
            "application/octet-stream",
            NOW,
        ).to_dict(),
    }
    for filename, document in documents.items():
        jsonschema.validate(
            document,
            _json(CONTRACTS / "logical-schema" / filename),
            cls=jsonschema.Draft202012Validator,
        )


@pytest.mark.conformance
def test_relation_profile_wire_contract() -> None:
    customers = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    orders = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    profile = RelationProfile("source", "target", False, (customers,), (orders,))
    jsonschema.validate(
        profile.to_dict(),
        _json(CONTRACTS / "logical-schema" / "meridian.relation-profile.v1.schema.json"),
        cls=jsonschema.Draft202012Validator,
    )


@pytest.mark.conformance
def test_catalog_and_public_api_ledgers_are_exact() -> None:
    registry_contract = _json(CONTRACTS / "catalogs" / "meridian-catalog-registry.v1.json")
    assert [item["name"] for item in registry_contract["catalogs"]] == list(catalog_registry())  # type: ignore[index]
    assert registry_contract["notCatalogs"] == [
        "artifact",
        "audit",
        "geospatial",
        "lineage",
        "media",
        "ontology",
        "projection",
        "provenance",
        "query",
        "relation",
        "search",
        "telemetry",
        "time-series",
    ]
    public = _json(CONTRACTS / "public-api" / "meridian-semantics.v1.json")
    assert public["version"] == semantics.__version__
    assert public["core"] == ">=1.0.1,<2"
    assert public["exports"] == sorted(semantics.__all__)
    assert set(public["catalogs"]) == {"structured", "cache"}  # type: ignore[arg-type]


@pytest.mark.conformance
def test_compatibility_ledger_and_packaged_contract_data() -> None:
    ledger = _json(ROOT / "compatibility.json")
    assert ledger["core"] == {
        "distribution": "meridian-storage-core",
        "version": "1.1.0",
        "publicContractCommit": "d30219cb39153a66f5adddd943a37c2fd24591b7",
        "sdistSha256": "46f7b64b706112dad0b62f768bfaf8b05f22d73d87f77702a0b3f7fd61544ca7",
        "wheelSha256": "fc7372a17993f43ec285e3d52c0e9c458ef8e6f5ce13bb2d1b01b064aa8eff49",
    }
    package_root = resources.files("meridian_storage.semantics")
    assert package_root.joinpath("compatibility.json").is_file()
    assert package_root.joinpath(
        "contracts", "logical-schema", "meridian.schema.v1.schema.json"
    ).is_file()


@pytest.mark.conformance
def test_cache_entry_contract_metadata_is_portable() -> None:
    entry = CacheEntry("key", {"value": 1}, "json", FINGERPRINT, NOW)
    assert set(entry.to_dict()) == {
        "key",
        "value",
        "serializerId",
        "schemaFingerprint",
        "createdAt",
        "expiresAt",
        "sourceVersion",
    }
    assert field("value").logical_type.kind.value == "string"
