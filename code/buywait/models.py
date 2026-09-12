from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .enums import AffordabilityStatus, Direction, EventStatus, PaymentMethod, RequestType


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    source_row: int


@dataclass(frozen=True)
class ExpectedSampleOutput:
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: date | None
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class SampleRequest:
    request: Request
    expected: ExpectedSampleOutput


@dataclass(frozen=True)
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...]
    expense_categories_to_protect: tuple[str, ...]
    expense_categories_user_is_willing_to_reduce: tuple[str, ...]
    expense_categories_user_is_willing_to_stop: tuple[str, ...]
    payment_methods_user_will_consider: tuple[PaymentMethod, ...]
    max_installment_months: int | None
    source_row: int


@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: Direction
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date | None
    status: EventStatus
    linked_event_id: str | None
    flexibility: str | None
    minimum_allowed_amount: Decimal | None
    source_row: int


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal
    source_row: int


@dataclass(frozen=True)
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal
    source_row: int


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str
    source_row: int


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    source_row: int

