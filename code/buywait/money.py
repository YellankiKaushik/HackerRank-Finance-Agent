from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import ParseError


def parse_money(value: str | None, *, field: str = "money") -> Decimal:
    """Parse a required CSV money value without using floats."""
    if value is None or value == "":
        raise ParseError(f"{field} is required and cannot be blank")
    if value != value.strip():
        raise ParseError(f"{field} has surrounding whitespace: {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ParseError(f"{field} is not a valid Decimal: {value!r}") from exc


def parse_optional_money(value: str | None, *, field: str = "money") -> Decimal | None:
    """Parse an optional CSV money value; blank stays None, not zero."""
    if value is None or value == "":
        return None
    return parse_money(value, field=field)


def format_decimal_plain(value: Decimal) -> str:
    """Render Decimal without exponent notation, preserving its scale."""
    return format(value, "f")


def format_decimal_compact(value: Decimal) -> str:
    """Generic compact Decimal renderer for diagnostics, not final output rules."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text

