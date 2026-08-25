# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from decimal import Decimal

import pytest

from meridian_storage.semantics import LocalizedText, SemanticVersion
from meridian_storage.semantics.canonical import (
    canonical_json_bytes,
    freeze_json,
    normalize_core_name,
    normalize_extension_key,
    normalize_unicode_name,
    sha256_fingerprint,
    sorted_json_mapping,
    thaw_json,
)
from meridian_storage.semantics.i18n import normalize_locale, validate_icu_message


def test_canonical_json_is_sorted_unicode_and_immutable() -> None:
    source = {"z": [1, {"é": True}], "a": "中文"}
    frozen = freeze_json(source)
    source["z"] = []
    assert thaw_json(frozen) == {"z": [1, {"é": True}], "a": "中文"}
    assert canonical_json_bytes(frozen) == (
        b'{"a":"\xe4\xb8\xad\xe6\x96\x87","z":[1,{"\xc3\xa9":true}]}'
    )
    assert sha256_fingerprint({"b": 2, "a": 1}) == sha256_fingerprint({"a": 1, "b": 2})
    assert tuple(sorted_json_mapping({"z": 1, "a": 2})) == ("a", "z")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), b"bytes"])
def test_freeze_rejects_non_json_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        freeze_json(value)


def test_freeze_rejects_cycles_and_non_string_keys() -> None:
    sequence: list[object] = []
    sequence.append(sequence)
    mapping: dict[str, object] = {}
    mapping["self"] = mapping
    with pytest.raises(ValueError, match="cyclic"):
        freeze_json(sequence)
    with pytest.raises(ValueError, match="cyclic"):
        freeze_json(mapping)
    with pytest.raises(TypeError, match="keys"):
        freeze_json({1: "value"})


def test_name_normalization_and_extension_namespaces() -> None:
    assert normalize_unicode_name("Cafe\u0301", "name") == "Café"
    assert normalize_core_name("tenant-1", "namespace") == "tenant-1"
    assert normalize_extension_key("org.example/profile") == "org.example/profile"
    assert normalize_extension_key("https://example.test/profile") == (
        "https://example.test/profile"
    )
    with pytest.raises(ValueError, match="control"):
        normalize_unicode_name("bad\nname", "name")
    with pytest.raises(ValueError, match="exceeds"):
        normalize_unicode_name("x" * 256, "name")
    with pytest.raises(ValueError, match="Core"):
        normalize_core_name("équipe", "namespace")
    with pytest.raises(ValueError, match="namespaced"):
        normalize_extension_key("profile")


def test_semver_precedence_identity_and_rendering() -> None:
    versions = [
        SemanticVersion.parse(item)
        for item in ("1.0.0", "1.0.0-alpha.1", "1.0.0-alpha", "0.9.9", "1.0.0-beta")
    ]
    assert [str(item) for item in sorted(versions)] == [
        "0.9.9",
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-beta",
        "1.0.0",
    ]
    assert SemanticVersion.parse("1.0.0+first") == SemanticVersion.parse("1.0.0+second")
    assert hash(SemanticVersion.parse("1.0.0+x")) == hash(SemanticVersion.parse("1.0.0+y"))
    assert SemanticVersion.parse("1.0.0-alpha") < SemanticVersion.parse("1.0.0-alpha.2")
    assert SemanticVersion.parse("1.0.0").__lt__(object()) is NotImplemented


@pytest.mark.parametrize(
    "value",
    ["1", "1.0", "01.0.0", "1.0.0-01", "1.0.0-alpha..1", "1.0.0+build..1", "v1.0.0"],
)
def test_semver_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        SemanticVersion.parse(value)
    with pytest.raises(TypeError):
        SemanticVersion.parse(1)


def test_canonical_json_rejects_utf8_surrogates() -> None:
    with pytest.raises(ValueError, match="surrogate"):
        canonical_json_bytes("\ud800")


def test_locale_normalization_and_fallback() -> None:
    text = LocalizedText(
        "en-US",
        {
            "en-US": "Color",
            "en": "Colour",
            "zh-Hans": "颜色",
        },
    )
    assert normalize_locale("ZH-hans-cn") == "zh-Hans-CN"
    assert text.resolve("zh-Hans-CN") == "颜色"
    assert text.resolve("fr-CA") == "Color"
    assert text.to_dict()["defaultLocale"] == "en-US"
    assert LocalizedText.from_mapping("Hello").resolve("de") == "Hello"
    assert LocalizedText.from_mapping(text.to_dict()) == text


def test_icu_plural_select_ordinal_and_quotes() -> None:
    message = LocalizedText(
        "en",
        {
            "en": (
                "{gender, select, female {She} male {He} other {They}} has "
                "{count, plural, offset:1 =0 {none} one {one with # peer} other {# items}}; "
                "rank {rank, selectordinal, one {#st} two {#nd} few {#rd} other {#th}}."
            )
        },
    )
    assert message.format("en", {"gender": "female", "count": 3, "rank": 22}) == (
        "She has 2 items; rank 22nd."
    )
    assert (
        LocalizedText("fr", {"fr": "{count, plural, one {un} other {plusieurs}}"}).format(
            "fr", {"count": Decimal(0)}
        )
        == "un"
    )
    assert (
        LocalizedText("zh", {"zh": "{count, plural, other {# 个}}"}).format("zh", {"count": 4})
        == "4 个"
    )
    assert validate_icu_message("This '{is}' literal and ''quoted''")


@pytest.mark.parametrize(
    "pattern",
    [
        "{",
        "}",
        "{count, plural, one {one}}",
        "{count, unknown, other {x}}",
        "{count, plural, bogus {x} other {y}}",
        "unterminated '",
    ],
)
def test_icu_rejects_invalid_messages(pattern: str) -> None:
    with pytest.raises(ValueError):
        validate_icu_message(pattern)


def test_localized_text_rejects_invalid_inputs_and_missing_values() -> None:
    with pytest.raises(ValueError, match="defaultLocale"):
        LocalizedText("en", {"fr": "Bonjour"})
    with pytest.raises(ValueError, match="duplicate"):
        LocalizedText("en", {"en": "one", "EN": "two"})
    with pytest.raises(ValueError, match="at least"):
        LocalizedText("en", {})
    with pytest.raises(TypeError):
        LocalizedText.from_mapping(3)
    with pytest.raises(KeyError, match="name"):
        LocalizedText("en", {"en": "Hello {name}"}).format("en", {})
    with pytest.raises(ValueError, match="numeric"):
        LocalizedText("en", {"en": "{n, plural, other {x}}"}).format("en", {"n": "x"})
