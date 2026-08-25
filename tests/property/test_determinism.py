# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from meridian_storage.semantics import (
    CatalogName,
    FieldDefinition,
    LogicalKind,
    LogicalType,
    SchemaDocument,
    SchemaReference,
    SemanticKind,
    canonical_json_bytes,
    sha256_fingerprint,
    validate_record,
)

UNICODE_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)))
JSON_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | UNICODE_TEXT
)
JSON_VALUES = st.recursive(
    JSON_SCALARS,
    lambda children: (
        st.lists(children, max_size=5)
        | st.dictionaries(UNICODE_TEXT.map(lambda item: item[:12]), children, max_size=5)
    ),
    max_leaves=15,
)


@given(JSON_VALUES)
def test_canonical_json_and_fingerprint_are_deterministic(value: object) -> None:
    assert canonical_json_bytes(value) == canonical_json_bytes(value)
    assert sha256_fingerprint(value) == sha256_fingerprint(value)


@given(
    st.dictionaries(
        UNICODE_TEXT.filter(bool).map(lambda item: item[:8]),
        JSON_SCALARS,
        max_size=8,
    )
)
def test_mapping_insertion_order_does_not_change_fingerprint(value: dict[str, object]) -> None:
    reversed_value = dict(reversed(tuple(value.items())))
    assert sha256_fingerprint(value) == sha256_fingerprint(reversed_value)


@given(
    st.lists(
        st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True), min_size=1, max_size=8, unique=True
    )
)
def test_schema_field_order_is_canonical(names: list[str]) -> None:
    if "id" not in names:
        names.append("id")
    fields = tuple(FieldDefinition(name, LogicalType(LogicalKind.STRING)) for name in names)
    reference = SchemaReference(CatalogName.STRUCTURED, "property", "entity", "1.0.0")
    left = SchemaDocument(reference, SemanticKind.RELATIONAL, fields, ("id",))
    right = SchemaDocument(reference, SemanticKind.RELATIONAL, tuple(reversed(fields)), ("id",))
    assert left.to_dict() == right.to_dict()
    assert left.fingerprint == right.fingerprint


@given(st.integers(min_value=-(2**31), max_value=2**31 - 1))
def test_int32_round_trip_is_exact(value: int) -> None:
    document = SchemaDocument(
        SchemaReference(CatalogName.STRUCTURED, "property", "integer", "1.0.0"),
        SemanticKind.RELATIONAL,
        (
            FieldDefinition("id", LogicalType(LogicalKind.STRING)),
            FieldDefinition("value", LogicalType(LogicalKind.INT32)),
        ),
        ("id",),
    )
    assert validate_record(document, {"id": "x", "value": value})["value"] == value
