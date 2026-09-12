class BuyWaitError(Exception):
    """Base error for strict loader and primitive failures."""


class ParseError(BuyWaitError):
    """Raised when a primitive value cannot be parsed."""


class SchemaError(BuyWaitError):
    """Raised when a CSV file does not match the expected schema."""


class IntegrityError(BuyWaitError):
    """Raised when loaded participant data is structurally inconsistent."""

