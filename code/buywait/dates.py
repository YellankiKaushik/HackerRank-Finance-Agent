from __future__ import annotations

from datetime import date, datetime

from .errors import ParseError


def parse_date(value: str | None, *, field: str = "date") -> date:
    """Parse a required challenge date in strict YYYY-MM-DD format."""
    if value is None or value == "":
        raise ParseError(f"{field} is required and cannot be blank")
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise ParseError(f"{field} must be YYYY-MM-DD: {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"{field} is not a valid date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ParseError(f"{field} must be canonical YYYY-MM-DD: {value!r}")
    return parsed


def parse_optional_date(value: str | None, *, field: str = "date") -> date | None:
    if value is None or value == "":
        return None
    return parse_date(value, field=field)


def parse_iso_timestamp(value: str | None, *, field: str = "timestamp") -> datetime:
    """Parse a required ISO timestamp without defining temporal cutoff policy."""
    if value is None or value == "":
        raise ParseError(f"{field} is required and cannot be blank")
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ParseError(f"{field} is not a valid ISO timestamp: {value!r}") from exc

