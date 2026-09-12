from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .enums import Direction
from .loaders import LoadedDataset
from .models import ImageRecord, Request
from .simulator import BaselineSimulation, simulate_baseline_for_request


@dataclass(frozen=True)
class RequestPayment:
    date: date
    amount: Decimal
    source: str = "request_payment"


@dataclass(frozen=True)
class PlanSafetyResult:
    safe: bool
    minimum_projected_balance: Decimal
    minimum_headroom: Decimal
    breach_date: date | None
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MissingAmountEvidence:
    event_id: str
    image_ids: tuple[str, ...]


@dataclass(frozen=True)
class CapacityResult:
    request_id: str
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    capacity_trustworthy: bool
    missing_amount_evidence: tuple[MissingAmountEvidence, ...]
    baseline_minimum_headroom: Decimal
    baseline_minimum_projected_balance: Decimal


def calculate_capacity_for_request(dataset: LoadedDataset, request: Request) -> CapacityResult:
    simulation = simulate_baseline_for_request(dataset, request)
    missing = _missing_amount_evidence(dataset, simulation)
    capacity = calculate_capacity_from_simulation(request, simulation, missing_amount_evidence=missing)
    _validate_optimized_capacity(request, simulation, capacity)
    return capacity


def calculate_capacity_from_simulation(
    request: Request,
    simulation: BaselineSimulation,
    *,
    missing_amount_evidence: tuple[MissingAmountEvidence, ...] = (),
) -> CapacityResult:
    safe_today = _safe_amount_on_date(simulation, request.request_date)
    amount_safe = min(request.requested_amount, max(Decimal("0"), safe_today))
    earliest = earliest_full_payment_date(request, simulation)
    return CapacityResult(
        request_id=request.request_id,
        amount_safe_to_pay=amount_safe,
        earliest_date_for_full_payment=earliest,
        capacity_trustworthy=not missing_amount_evidence,
        missing_amount_evidence=missing_amount_evidence,
        baseline_minimum_headroom=simulation.minimum_headroom,
        baseline_minimum_projected_balance=simulation.minimum_projected_balance,
    )


def earliest_full_payment_date(request: Request, simulation: BaselineSimulation) -> date | None:
    prefix_safe = True
    for day in simulation.days:
        if prefix_safe and request.requested_amount <= _safe_amount_on_date(simulation, day.date):
            return day.date
        if day.headroom < Decimal("0"):
            prefix_safe = False
    return None


def one_time_payment_is_safe(simulation: BaselineSimulation, payment_date: date, amount: Decimal) -> bool:
    return plan_safety(simulation, (RequestPayment(payment_date, amount),)).safe


def plan_safety(
    simulation: BaselineSimulation,
    payments: Iterable[RequestPayment],
) -> PlanSafetyResult:
    payment_tuple = tuple(sorted(payments, key=lambda item: (item.date, item.source, item.amount)))
    reasons: list[str] = []
    if any(payment.amount < Decimal("0") for payment in payment_tuple):
        reasons.append("negative_payment_amount")
    if any(payment.date < simulation.request_date for payment in payment_tuple):
        reasons.append("payment_before_request_date")
    if any(payment.date > simulation.horizon_end for payment in payment_tuple):
        reasons.append("payment_after_horizon")

    cumulative = Decimal("0")
    index = 0
    minimum_balance: Decimal | None = None
    minimum_headroom: Decimal | None = None
    breach_date: date | None = None
    for day in simulation.days:
        while index < len(payment_tuple) and payment_tuple[index].date == day.date:
            cumulative += payment_tuple[index].amount
            index += 1
        projected_balance = day.closing_balance - cumulative
        headroom = projected_balance - day.minimum_balance_to_keep
        if minimum_balance is None or projected_balance < minimum_balance:
            minimum_balance = projected_balance
        if minimum_headroom is None or headroom < minimum_headroom:
            minimum_headroom = headroom
        if headroom < Decimal("0") and breach_date is None:
            breach_date = day.date

    if breach_date is not None:
        reasons.append("minimum_balance_breach")
    return PlanSafetyResult(
        safe=not reasons,
        minimum_projected_balance=minimum_balance if minimum_balance is not None else Decimal("0"),
        minimum_headroom=minimum_headroom if minimum_headroom is not None else Decimal("0"),
        breach_date=breach_date,
        rejection_reasons=tuple(reasons),
    )


def _safe_amount_on_date(simulation: BaselineSimulation, payment_date: date) -> Decimal:
    return simulation.suffix_min_headroom.get(payment_date, Decimal("-1"))


def _validate_optimized_capacity(
    request: Request,
    simulation: BaselineSimulation,
    capacity: CapacityResult,
) -> None:
    if capacity.amount_safe_to_pay > Decimal("0"):
        assert one_time_payment_is_safe(simulation, request.request_date, capacity.amount_safe_to_pay)
    if capacity.amount_safe_to_pay < request.requested_amount:
        penny_more = capacity.amount_safe_to_pay + Decimal("0.01")
        if penny_more <= request.requested_amount:
            assert not one_time_payment_is_safe(simulation, request.request_date, penny_more)
    if capacity.earliest_date_for_full_payment is not None:
        assert one_time_payment_is_safe(simulation, capacity.earliest_date_for_full_payment, request.requested_amount)


def _missing_amount_evidence(
    dataset: LoadedDataset,
    simulation: BaselineSimulation,
) -> tuple[MissingAmountEvidence, ...]:
    by_event: dict[str, list[ImageRecord]] = {}
    for image in dataset.images:
        if image.related_event_id:
            by_event.setdefault(image.related_event_id, []).append(image)
    return tuple(
        MissingAmountEvidence(
            event_id=event_id,
            image_ids=tuple(image.image_id for image in sorted(by_event.get(event_id, ()), key=lambda item: (item.source_row, item.image_id))),
        )
        for event_id in simulation.skipped_missing_amount_event_ids
    )


def payment_plan_total(payments: Iterable[RequestPayment]) -> Decimal:
    return sum((payment.amount for payment in payments), Decimal("0"))


def payment_plan_completion_date(payments: Iterable[RequestPayment]) -> date | None:
    payment_tuple = tuple(payments)
    if not payment_tuple:
        return None
    return max(payment.date for payment in payment_tuple)
