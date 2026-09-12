from __future__ import annotations

from enum import Enum

from .errors import ParseError


class StrictStrEnum(str, Enum):
    @classmethod
    def parse(cls, value: str, *, field: str) -> "StrictStrEnum":
        try:
            return cls(value)
        except ValueError as exc:
            allowed = ", ".join(member.value for member in cls)
            raise ParseError(f"{field} has unknown value {value!r}; expected one of: {allowed}") from exc


class RequestType(StrictStrEnum):
    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


class EventStatus(StrictStrEnum):
    CANCELLED = "cancelled"
    FAILED = "failed"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    SETTLED = "settled"
    UNREALIZED = "unrealized"


class Direction(StrictStrEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    NON_CASH = "non_cash"


class PaymentMethod(StrictStrEnum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class AffordabilityStatus(StrictStrEnum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"

