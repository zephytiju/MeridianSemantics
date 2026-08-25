# SPDX-License-Identifier: Apache-2.0
"""Portable multilingual metadata with validated ICU MessageFormat patterns."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast

from .canonical import JsonValue, normalize_unicode_name

_LOCALE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_ARGUMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_SELECTOR_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*|=-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)$")


@dataclass(frozen=True, slots=True)
class _Argument:
    name: str
    kind: str = "simple"
    style: str = ""
    options: Mapping[str, tuple[_Node, ...]] = field(default_factory=dict)
    offset: Decimal = Decimal(0)


type _Node = str | _Argument


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.position = 0

    def parse(self, *, nested: bool = False) -> tuple[_Node, ...]:
        nodes: list[_Node] = []
        text: list[str] = []
        quoted = False
        while self.position < len(self.pattern):
            character = self.pattern[self.position]
            if character == "'":
                if self._peek("''"):
                    text.append("'")
                    self.position += 2
                    continue
                quoted = not quoted
                self.position += 1
                continue
            if not quoted and character == "{":
                if text:
                    nodes.append("".join(text))
                    text.clear()
                nodes.append(self._argument())
                continue
            if not quoted and character == "}":
                if not nested:
                    raise ValueError(f"unexpected closing brace at offset {self.position}")
                self.position += 1
                if text:
                    nodes.append("".join(text))
                return tuple(nodes)
            text.append(character)
            self.position += 1
        if quoted:
            raise ValueError("unterminated ICU apostrophe quote")
        if nested:
            raise ValueError("unterminated ICU argument option")
        if text:
            nodes.append("".join(text))
        return tuple(nodes)

    def _argument(self) -> _Argument:
        self.position += 1
        name = self._token({",", "}"}).strip()
        if _ARGUMENT_RE.fullmatch(name) is None:
            raise ValueError(f"invalid ICU argument name: {name!r}")
        terminator = self._current()
        if terminator == "}":
            self.position += 1
            return _Argument(name)
        self.position += 1
        kind = self._token({",", "}"}).strip()
        if not kind:
            raise ValueError(f"ICU argument {name!r} has no type")
        terminator = self._current()
        if kind not in {"plural", "selectordinal", "select"}:
            if terminator == "}":
                self.position += 1
                return _Argument(name, kind=kind)
            self.position += 1
            style = self._token({"}"}).strip()
            self.position += 1
            return _Argument(name, kind=kind, style=style)
        if terminator != ",":
            raise ValueError(f"ICU {kind} argument {name!r} requires selector options")
        self.position += 1
        options: dict[str, tuple[_Node, ...]] = {}
        offset = Decimal(0)
        while True:
            self._skip_space()
            if self._current() == "}":
                self.position += 1
                break
            selector = self._token({"{", " ", "\t", "\r", "\n"}).strip()
            if selector.startswith("offset:"):
                if kind == "select":
                    raise ValueError("ICU select does not permit plural offset")
                try:
                    offset = Decimal(selector.split(":", 1)[1])
                except InvalidOperation as exc:
                    raise ValueError("invalid ICU plural offset") from exc
                if not offset.is_finite() or offset < 0:
                    raise ValueError("ICU plural offset must be a non-negative finite number")
                continue
            if _SELECTOR_RE.fullmatch(selector) is None:
                raise ValueError(f"invalid ICU selector: {selector!r}")
            if (
                kind in {"plural", "selectordinal"}
                and not selector.startswith("=")
                and selector not in {"zero", "one", "two", "few", "many", "other"}
            ):
                raise ValueError(f"invalid ICU plural category: {selector!r}")
            self._skip_space()
            if self._current() != "{":
                raise ValueError(f"ICU selector {selector!r} requires a message body")
            self.position += 1
            if selector in options:
                raise ValueError(f"duplicate ICU selector: {selector!r}")
            options[selector] = self.parse(nested=True)
        if "other" not in options:
            raise ValueError(f"ICU {kind} argument {name!r} requires an other selector")
        return _Argument(
            name,
            kind=kind,
            options=MappingProxyType(options),
            offset=offset,
        )

    def _token(self, stops: set[str]) -> str:
        start = self.position
        while self.position < len(self.pattern) and self.pattern[self.position] not in stops:
            self.position += 1
        if self.position >= len(self.pattern):
            raise ValueError("unterminated ICU argument")
        return self.pattern[start : self.position]

    def _skip_space(self) -> None:
        while self.position < len(self.pattern) and self.pattern[self.position].isspace():
            self.position += 1

    def _current(self) -> str:
        if self.position >= len(self.pattern):
            raise ValueError("unterminated ICU argument")
        return self.pattern[self.position]

    def _peek(self, value: str) -> bool:
        return self.pattern.startswith(value, self.position)


def normalize_locale(value: object) -> str:
    locale = normalize_unicode_name(value, "locale", maximum=64)
    if _LOCALE_RE.fullmatch(locale) is None:
        raise ValueError(f"invalid BCP 47 locale: {locale!r}")
    parts = locale.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif len(part) in {2, 3} and part.isalnum():
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def validate_icu_message(pattern: object) -> str:
    message = normalize_unicode_name(pattern, "ICU message", maximum=16_384)
    parser = _Parser(message)
    parser.parse()
    return message


@dataclass(frozen=True, slots=True)
class LocalizedText:
    """One ICU message per locale with deterministic fallback behavior."""

    default_locale: str
    messages: Mapping[str, str]
    _compiled: Mapping[str, tuple[_Node, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        default = normalize_locale(self.default_locale)
        messages: dict[str, str] = {}
        compiled: dict[str, tuple[_Node, ...]] = {}
        for locale, message in self.messages.items():
            normalized_locale = normalize_locale(locale)
            if normalized_locale in messages:
                raise ValueError(f"duplicate normalized locale: {normalized_locale!r}")
            normalized_message = validate_icu_message(message)
            messages[normalized_locale] = normalized_message
            compiled[normalized_locale] = _Parser(normalized_message).parse()
        if not messages:
            raise ValueError("localized metadata requires at least one message")
        if default not in messages:
            raise ValueError("defaultLocale must have an exact message")
        object.__setattr__(self, "default_locale", default)
        object.__setattr__(self, "messages", MappingProxyType(dict(sorted(messages.items()))))
        object.__setattr__(self, "_compiled", MappingProxyType(dict(sorted(compiled.items()))))

    @classmethod
    def from_mapping(cls, value: object, *, default_locale: str = "en") -> LocalizedText:
        if isinstance(value, str):
            return cls(default_locale, {default_locale: value})
        if not isinstance(value, Mapping):
            raise TypeError("localized text must be a string or mapping")
        if set(value) == {"defaultLocale", "messages"}:
            messages = value["messages"]
            if not isinstance(messages, Mapping):
                raise TypeError("localized messages must be an object")
            return cls(
                cast(str, value["defaultLocale"]),
                {cast(str, key): cast(str, item) for key, item in messages.items()},
            )
        return cls(
            default_locale,
            {cast(str, key): cast(str, item) for key, item in value.items()},
        )

    def resolve(self, locale: str) -> str:
        return self.messages[self._resolve_locale(locale)]

    def _resolve_locale(self, locale: str) -> str:
        selected = normalize_locale(locale)
        candidates = [selected]
        while "-" in selected:
            selected = selected.rsplit("-", 1)[0]
            candidates.append(selected)
        candidates.append(self.default_locale)
        for candidate in candidates:
            if candidate in self.messages:
                return candidate
        raise AssertionError("default locale invariant was not preserved")

    def format(self, locale: str, values: Mapping[str, object]) -> str:
        selected_locale = self._resolve_locale(locale)
        return _render(self._compiled[selected_locale], values, selected_locale, pound=None)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "defaultLocale": self.default_locale,
            "messages": dict(self.messages),
        }


def _render(
    nodes: tuple[_Node, ...],
    values: Mapping[str, object],
    locale: str,
    *,
    pound: Decimal | None,
) -> str:
    result: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            result.append(node.replace("#", _decimal_text(pound)) if pound is not None else node)
            continue
        if node.name not in values:
            raise KeyError(f"missing ICU argument: {node.name}")
        value = values[node.name]
        if node.kind == "simple":
            result.append(str(value))
            continue
        if node.kind not in {"plural", "selectordinal", "select"}:
            result.append(str(value))
            continue
        if node.kind == "select":
            selector = str(value)
            selected = node.options.get(selector, node.options["other"])
            result.append(_render(selected, values, locale, pound=pound))
            continue
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"ICU plural argument {node.name!r} must be numeric") from exc
        exact = f"={_decimal_text(number)}"
        selector = exact if exact in node.options else _plural_category(locale, number, node.kind)
        selected = node.options.get(selector, node.options["other"])
        result.append(_render(selected, values, locale, pound=number - node.offset))
    return "".join(result)


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return "#"
    normalized = value.normalize()
    return format(normalized, "f")


def _plural_category(locale: str, number: Decimal, kind: str) -> str:
    language = locale.split("-", 1)[0]
    integer = int(number)
    if kind == "selectordinal" and language == "en":
        mod10, mod100 = integer % 10, integer % 100
        if mod10 == 1 and mod100 != 11:
            return "one"
        if mod10 == 2 and mod100 != 12:
            return "two"
        if mod10 == 3 and mod100 != 13:
            return "few"
        return "other"
    if language in {"zh", "ja", "ko", "th", "vi"}:
        return "other"
    if language in {"fr", "pt"}:
        return "one" if number in {Decimal(0), Decimal(1)} else "other"
    if language in {"ru", "uk"} and number == integer:
        if integer % 10 == 1 and integer % 100 != 11:
            return "one"
        if integer % 10 in {2, 3, 4} and integer % 100 not in {12, 13, 14}:
            return "few"
        if integer % 10 == 0 or integer % 10 >= 5 or integer % 100 in {11, 12, 13, 14}:
            return "many"
    return "one" if number == 1 else "other"


__all__ = ["LocalizedText", "normalize_locale", "validate_icu_message"]
