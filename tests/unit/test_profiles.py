# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from meridian_storage.semantics import (
    CacheProfile,
    CatalogName,
    DocumentProfile,
    FullTextProfile,
    GeospatialProfile,
    KeyValueProfile,
    LocalizedText,
    ObjectProfile,
    RelationalProfile,
    RelationProfile,
    ResourceReference,
    SemanticKind,
    TimeSeriesProfile,
    profile_from_mapping,
)


@pytest.mark.parametrize(
    "profile",
    [
        RelationalProfile(alternate_keys=(("external_id",),), unique_fields=(("email",),)),
        DocumentProfile("body", indexed_paths=("/customer/name", "/tags/0")),
        KeyValueProfile("key", "value", "expires_at", ordered=True, max_multi_get=50),
        FullTextProfile(
            ("title", "body"),
            normalized_field="normalized",
            facets=("kind",),
            highlights=("body",),
            display_name=LocalizedText("en", {"en": "Search"}),
        ),
        GeospatialProfile(("location",)),
        TimeSeriesProfile("observed_at", ("device",), ("region",), ("temperature",)),
        RelationProfile(
            "source",
            "target",
            False,
            (ResourceReference(CatalogName.STRUCTURED, "example", "customers"),),
            (ResourceReference(CatalogName.STRUCTURED, "example", "orders"),),
        ),
        ObjectProfile(metadata={"retention": "durable"}),
        ObjectProfile(profile="artifact"),
        ObjectProfile(profile="media", mutability="mutable"),
        CacheProfile(consistency="session"),
    ],
)
def test_profile_mapping_round_trip(profile: object) -> None:
    payload = profile.to_dict()  # type: ignore[union-attr]
    parsed = profile_from_mapping(payload)
    assert parsed.to_dict() == payload


def test_relation_neighbors_honor_directionality() -> None:
    customers = ResourceReference(CatalogName.STRUCTURED, "example", "customers")
    orders = ResourceReference(CatalogName.STRUCTURED, "example", "orders")
    directed = RelationProfile("source", "target", True, (customers,), (orders,))
    undirected = RelationProfile("source", "target", False, (customers,), (orders,))
    assert directed.neighbors(customers) == (orders,)
    assert directed.neighbors(orders) == ()
    assert undirected.neighbors(orders) == (customers,)


@pytest.mark.parametrize(
    "factory,match",
    [
        (lambda: RelationalProfile(alternate_keys=(("id",), ("id",))), "unique"),
        (lambda: DocumentProfile("body", unknown_fields="anything"), "unknownFields"),
        (lambda: DocumentProfile("body", indexed_paths=("not-a-pointer",)), "Pointers"),
        (lambda: DocumentProfile("body", indexed_paths=("/~2",)), "escape"),
        (lambda: KeyValueProfile("same", "same"), "differ"),
        (lambda: KeyValueProfile("key", "value", max_multi_get=0), "maxMultiGet"),
        (lambda: FullTextProfile((), analyzer_profile="ascii"), "cannot be empty"),
        (lambda: FullTextProfile(("body",), language_hints=()), "languageHints"),
        (lambda: GeospatialProfile(("point",), distance_unit="km"), "meters"),
        (lambda: GeospatialProfile(("point",), operations=("unknown",)), "unsupported"),
        (
            lambda: TimeSeriesProfile("time", ("series",), ("series",), ("value",)),
            "disjoint",
        ),
        (
            lambda: RelationProfile(
                "edge",
                "edge",
                True,
                (ResourceReference(CatalogName.STRUCTURED, "example", "a"),),
                (ResourceReference(CatalogName.STRUCTURED, "example", "b"),),
            ),
            "differ",
        ),
        (lambda: ObjectProfile(profile="archive"), "profile"),
        (lambda: ObjectProfile(profile="artifact", mutability="mutable"), "publish-once"),
        (lambda: CacheProfile(authoritative=True), "never authoritative"),
    ],
)
def test_profile_invariants(factory: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_profile_parser_rejects_missing_unknown_and_wrong_values() -> None:
    with pytest.raises(ValueError, match="requires kind"):
        profile_from_mapping({})
    with pytest.raises(ValueError, match="unknown"):
        profile_from_mapping({"kind": "document", "bodyField": "body", "extra": 1})
    with pytest.raises(ValueError):
        profile_from_mapping({"kind": "ontology"})
    with pytest.raises(TypeError, match="arrays"):
        profile_from_mapping({"kind": "relational", "alternateKeys": "id"})
    assert profile_from_mapping({"kind": SemanticKind.CACHE.value}).kind is SemanticKind.CACHE
