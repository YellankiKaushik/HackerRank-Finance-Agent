from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from .capacity import (
    CapacityResult,
    RequestPayment,
    calculate_capacity_from_simulation,
    calculate_capacity_for_request,
    plan_safety,
)
from .enums import AffordabilityStatus, Direction, PaymentMethod
from .loaders import LoadedDataset
from .models import FinancialEvent, FinancialProfile, PaymentOption, Request
from .money import format_decimal_compact, format_decimal_plain
from .simulator import (
    BaselineSimulation,
    DailyBalance,
    LedgerOccurrence,
    _occurrence_sort_key,
    _simulate_days,
    _suffix_min_headroom,
    simulate_baseline_for_request,
)


@dataclass(frozen=True)
class SpendingChange:
    action: str
    event_id: str
    new_amount: Decimal | None = None

    def render(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        if self.action == "reduce_to" and self.new_amount is not None:
            return f"reduce_to:{self.event_id}:{_format_plan_amount(self.new_amount)}"
        raise ValueError(f"Unsupported spending change {self}")


@dataclass(frozen=True)
class CandidatePlan:
    method: PaymentMethod
    payment_option_id: str | None
    payments: tuple[RequestPayment, ...]
    total_payable: Decimal
    first_payment_date: date | None
    completion_date: date | None
    spending_changes: tuple[SpendingChange, ...]
    minimum_projected_balance: Decimal
    preference_eligible: bool
    deadline_eligible: bool
    financially_safe: bool
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionRow:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: date | None
    spending_changes_needed: str
    decision_explanation: str
    candidate: CandidatePlan | None


def decide_request(dataset: LoadedDataset, request: Request) -> DecisionRow:
    baseline = simulate_baseline_for_request(dataset, request)
    capacity = calculate_capacity_for_request(dataset, request)
    profile = dataset.profile_by_user[request.user_id]
    candidates = generate_candidates(dataset, request, baseline, capacity)
    ranked = sorted(candidates, key=lambda item: _candidate_rank_key(request, item))
    for candidate in ranked:
        if not _candidate_selectable(candidate):
            continue
        from .verifier import verify_decision

        row = _row_for_candidate(request, capacity, candidate)
        verified = verify_decision(dataset, request, row, baseline=baseline)
        if verified.valid:
            return row
    fallback = _fallback_row(request, capacity, profile, baseline)
    from .verifier import verify_decision

    verified = verify_decision(dataset, request, fallback, baseline=baseline)
    if not verified.valid:
        raise ValueError(f"Fallback failed verification for {request.request_id}: {verified.errors}")
    return fallback


def generate_candidates(
    dataset: LoadedDataset,
    request: Request,
    baseline: BaselineSimulation,
    capacity: CapacityResult,
) -> tuple[CandidatePlan, ...]:
    profile = dataset.profile_by_user[request.user_id]
    candidates: list[CandidatePlan] = []
    candidates.extend(_baseline_candidates(dataset, request, profile, baseline, capacity))
    for candidate in tuple(candidates):
        if not candidate.financially_safe:
            candidates.extend(_spending_change_variants(dataset, request, profile, baseline, candidate))
    return tuple(candidates)


def _baseline_candidates(
    dataset: LoadedDataset,
    request: Request,
    profile: FinancialProfile,
    baseline: BaselineSimulation,
    capacity: CapacityResult,
) -> tuple[CandidatePlan, ...]:
    candidates: list[CandidatePlan] = []
    accepts_full = PaymentMethod.FULL_PAYMENT in profile.payment_methods_user_will_consider
    if accepts_full:
        candidates.append(
            _candidate(
                request,
                baseline,
                method=PaymentMethod.FULL_PAYMENT,
                payments=(RequestPayment(request.request_date, request.requested_amount),),
            )
        )
        if capacity.earliest_date_for_full_payment and capacity.earliest_date_for_full_payment > request.request_date:
            candidates.append(
                _candidate(
                    request,
                    baseline,
                    method=PaymentMethod.WAIT,
                    payments=(RequestPayment(capacity.earliest_date_for_full_payment, request.requested_amount),),
                )
            )
    if (
        request.allows_partial_payment
        and PaymentMethod.PARTIAL_PAYMENT in profile.payment_methods_user_will_consider
        and Decimal("0") < capacity.amount_safe_to_pay < request.requested_amount
        and capacity.earliest_date_for_full_payment is not None
        and capacity.earliest_date_for_full_payment <= request.desired_completion_date
    ):
        remaining = request.requested_amount - capacity.amount_safe_to_pay
        candidates.append(
            _candidate(
                request,
                baseline,
                method=PaymentMethod.PARTIAL_PAYMENT,
                payments=(
                    RequestPayment(request.request_date, capacity.amount_safe_to_pay),
                    RequestPayment(capacity.earliest_date_for_full_payment, remaining),
                ),
            )
        )
    for option in dataset.payment_options_by_request.get(request.request_id, ()):
        if option.payment_method is PaymentMethod.INSTALLMENTS:
            candidates.append(_installment_candidate(request, profile, baseline, option))
        elif option.payment_method is PaymentMethod.FULL_PAYMENT and accepts_full:
            candidates.append(
                _candidate(
                    request,
                    baseline,
                    method=PaymentMethod.FULL_PAYMENT,
                    payment_option_id=option.payment_option_id,
                    payments=(RequestPayment(option.first_payment_date, option.payment_amount, option.payment_option_id),),
                    total_payable=option.total_payable_amount,
                )
            )
    return tuple(candidates)


def _installment_candidate(
    request: Request,
    profile: FinancialProfile,
    baseline: BaselineSimulation,
    option: PaymentOption,
) -> CandidatePlan:
    payments: list[RequestPayment] = []
    current = option.first_payment_date
    for idx in range(option.number_of_payments):
        payments.append(RequestPayment(current, option.payment_amount, option.payment_option_id))
        if idx < option.number_of_payments - 1:
            current += timedelta(days=option.payment_frequency_days or 0)
    completion = payments[-1].date if payments else None
    preference = PaymentMethod.INSTALLMENTS in profile.payment_methods_user_will_consider
    if profile.max_installment_months is not None and completion is not None:
        max_completion = add_calendar_months(option.first_payment_date, profile.max_installment_months)
        preference = preference and completion <= max_completion
    return _candidate(
        request,
        baseline,
        method=PaymentMethod.INSTALLMENTS,
        payment_option_id=option.payment_option_id,
        payments=tuple(payments),
        total_payable=option.total_payable_amount,
        preference_eligible=preference,
    )


def _candidate(
    request: Request,
    baseline: BaselineSimulation,
    *,
    method: PaymentMethod,
    payments: tuple[RequestPayment, ...],
    payment_option_id: str | None = None,
    total_payable: Decimal | None = None,
    spending_changes: tuple[SpendingChange, ...] = (),
    adjusted_baseline: BaselineSimulation | None = None,
    preference_eligible: bool = True,
) -> CandidatePlan:
    simulation = adjusted_baseline or baseline
    safety = plan_safety(simulation, payments)
    financially_safe = safety.safe
    first = min((payment.date for payment in payments), default=None)
    completion = max((payment.date for payment in payments), default=None)
    total = total_payable if total_payable is not None else sum((payment.amount for payment in payments), Decimal("0"))
    deadline = completion is not None and completion <= request.desired_completion_date
    if not financially_safe:
        reasons = list(safety.rejection_reasons)
    else:
        reasons = []
    if not preference_eligible:
        reasons.append("payment_method_not_preferred")
    if not deadline:
        reasons.append("after_desired_completion_date")
    return CandidatePlan(
        method=method,
        payment_option_id=payment_option_id,
        payments=tuple(sorted(payments, key=lambda item: (item.date, item.source, item.amount))),
        total_payable=total,
        first_payment_date=first,
        completion_date=completion,
        spending_changes=spending_changes,
        minimum_projected_balance=safety.minimum_projected_balance,
        preference_eligible=preference_eligible,
        deadline_eligible=deadline,
        financially_safe=financially_safe,
        rejection_reasons=tuple(reasons),
    )


def _spending_change_variants(
    dataset: LoadedDataset,
    request: Request,
    profile: FinancialProfile,
    baseline: BaselineSimulation,
    candidate: CandidatePlan,
) -> tuple[CandidatePlan, ...]:
    if candidate.financially_safe or candidate.method is PaymentMethod.WAIT:
        return ()
    actions = _flexible_actions(dataset, profile, baseline)
    if not actions:
        return ()
    variants: list[CandidatePlan] = []
    for combo in _action_combinations(actions, max_size=3):
        if _has_conflicting_actions(combo):
            continue
        adjusted = apply_spending_changes(baseline, combo, profile)
        revised = _candidate(
            request,
            baseline,
            method=candidate.method,
            payment_option_id=candidate.payment_option_id,
            payments=candidate.payments,
            total_payable=candidate.total_payable,
            spending_changes=combo,
            adjusted_baseline=adjusted,
            preference_eligible=candidate.preference_eligible,
        )
        if revised.financially_safe:
            variants.append(revised)
    return tuple(variants)


def _flexible_actions(
    dataset: LoadedDataset,
    profile: FinancialProfile,
    baseline: BaselineSimulation,
) -> tuple[SpendingChange, ...]:
    protected = set(profile.expense_categories_to_protect)
    reducible = set(profile.expense_categories_user_is_willing_to_reduce)
    stoppable = set(profile.expense_categories_user_is_willing_to_stop)
    seen: set[tuple[str, str, Decimal | None]] = set()
    actions: list[SpendingChange] = []
    for occurrence in baseline.occurrences:
        if occurrence.source_type not in {"inferred_recurrence", "variable_essential_forecast"}:
            continue
        event = _action_event(dataset, occurrence)
        if event is None or event.category in protected:
            continue
        if event.flexibility in {"stoppable", "reducible_or_stoppable"} and event.category in stoppable:
            action = SpendingChange("stop", event.event_id)
            key = (action.action, action.event_id, action.new_amount)
            if key not in seen:
                actions.append(action)
                seen.add(key)
        if event.flexibility in {"reducible", "reducible_or_stoppable"} and event.category in reducible:
            floor = event.minimum_allowed_amount if isinstance(event.minimum_allowed_amount, Decimal) else Decimal("0")
            if isinstance(event.amount, Decimal) and floor < event.amount:
                for amount in {floor, (event.amount / Decimal("2")).quantize(Decimal("0.01"))}:
                    action = SpendingChange("reduce_to", event.event_id, amount)
                    key = (action.action, action.event_id, action.new_amount)
                    if key not in seen:
                        actions.append(action)
                        seen.add(key)
    return tuple(actions[:18])


def _action_event(dataset: LoadedDataset, occurrence: LedgerOccurrence) -> FinancialEvent | None:
    for event_id in reversed(occurrence.source_ids):
        event = dataset.event_by_id.get(event_id)
        if event is not None and event.flexibility:
            return event
    return None


def _action_combinations(actions: tuple[SpendingChange, ...], *, max_size: int) -> Iterable[tuple[SpendingChange, ...]]:
    for action in actions:
        yield (action,)
    if max_size >= 2:
        for i, first in enumerate(actions):
            for second in actions[i + 1 :]:
                yield (first, second)
    if max_size >= 3:
        for i, first in enumerate(actions):
            for j, second in enumerate(actions[i + 1 :], start=i + 1):
                for third in actions[j + 1 :]:
                    yield (first, second, third)


def _has_conflicting_actions(actions: tuple[SpendingChange, ...]) -> bool:
    seen: dict[str, str] = {}
    for action in actions:
        previous = seen.get(action.event_id)
        if previous is not None and previous != action.action:
            return True
        seen[action.event_id] = action.action
    return False


def apply_spending_changes(
    baseline: BaselineSimulation,
    changes: tuple[SpendingChange, ...],
    profile: FinancialProfile,
) -> BaselineSimulation:
    if not changes:
        return baseline
    by_event = {change.event_id: change for change in changes}
    adjusted: list[LedgerOccurrence] = []
    for occurrence in baseline.occurrences:
        change = next((by_event[event_id] for event_id in occurrence.source_ids if event_id in by_event), None)
        if change is None or occurrence.date < baseline.request_date or occurrence.direction is not Direction.DEBIT:
            adjusted.append(occurrence)
            continue
        if change.action == "stop":
            continue
        if change.action == "reduce_to" and change.new_amount is not None:
            amount = change.new_amount
            ratio = Decimal("1") if occurrence.amount == 0 else amount / occurrence.amount
            adjusted.append(
                replace(
                    occurrence,
                    amount=amount,
                    home_currency_amount=(occurrence.home_currency_amount * ratio).quantize(Decimal("0.01")),
                    reason_included=occurrence.reason_included + ":reduced",
                )
            )
            continue
        adjusted.append(occurrence)
    occurrences = tuple(sorted(adjusted, key=_occurrence_sort_key))
    days = _simulate_days(baseline.request_id, baseline.request_date, baseline.horizon_end, profile, occurrences)
    return replace(
        baseline,
        occurrences=occurrences,
        days=days,
        suffix_min_headroom=_suffix_min_headroom(days),
    )


def _candidate_selectable(candidate: CandidatePlan) -> bool:
    return candidate.preference_eligible and candidate.deadline_eligible and candidate.financially_safe


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _candidate_rank_key(request: Request, candidate: CandidatePlan) -> tuple[object, ...]:
    completes = candidate.completion_date is not None and candidate.completion_date <= request.desired_completion_date
    return (
        0 if completes else 1,
        0 if not candidate.spending_changes else 1,
        candidate.total_payable,
        candidate.first_payment_date or date.max,
        len(candidate.payments),
        candidate.payment_option_id or "",
        candidate.method.value,
    )


def _row_for_candidate(request: Request, capacity: CapacityResult, candidate: CandidatePlan) -> DecisionRow:
    status = _status_for_candidate(request, capacity, candidate)
    return DecisionRow(
        request_id=request.request_id,
        amount_safe_to_pay=capacity.amount_safe_to_pay,
        affordability_status=status,
        recommended_payment_method=candidate.method,
        payment_plan=format_payment_plan(candidate.payments),
        earliest_date_for_full_payment=capacity.earliest_date_for_full_payment,
        spending_changes_needed=format_spending_changes(candidate.spending_changes),
        decision_explanation=_explanation(request, capacity, candidate, status),
        candidate=candidate,
    )


def _status_for_candidate(
    request: Request,
    capacity: CapacityResult,
    candidate: CandidatePlan,
) -> AffordabilityStatus:
    if (
        candidate.method is PaymentMethod.FULL_PAYMENT
        and candidate.first_payment_date == request.request_date
        and not candidate.spending_changes
        and capacity.amount_safe_to_pay >= request.requested_amount
    ):
        return AffordabilityStatus.AFFORDABLE_NOW
    if candidate.method is PaymentMethod.WAIT:
        return AffordabilityStatus.AFFORDABLE_LATER
    return AffordabilityStatus.AFFORDABLE_WITH_PLAN


def _fallback_row(
    request: Request,
    capacity: CapacityResult,
    profile: FinancialProfile,
    baseline: BaselineSimulation,
) -> DecisionRow:
    explanation = (
        f"Do not proceed now. No eligible payment plan keeps the {profile.home_currency} "
        f"{format_decimal_compact(profile.minimum_balance_to_keep)} minimum protected."
    )
    return DecisionRow(
        request_id=request.request_id,
        amount_safe_to_pay=capacity.amount_safe_to_pay,
        affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
        recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
        payment_plan="none",
        earliest_date_for_full_payment=capacity.earliest_date_for_full_payment,
        spending_changes_needed="none",
        decision_explanation=explanation,
        candidate=None,
    )


def _explanation(
    request: Request,
    capacity: CapacityResult,
    candidate: CandidatePlan,
    status: AffordabilityStatus,
) -> str:
    if status is AffordabilityStatus.AFFORDABLE_NOW:
        return f"Pay {format_decimal_compact(request.requested_amount)} today; the minimum balance remains protected."
    if candidate.method is PaymentMethod.WAIT:
        return f"Wait until {candidate.first_payment_date} to pay {format_decimal_compact(request.requested_amount)} in full safely."
    if candidate.method is PaymentMethod.PARTIAL_PAYMENT:
        return "Use the two-payment partial plan; each payment was verified against the projected balance."
    if candidate.method is PaymentMethod.INSTALLMENTS:
        return f"Use {len(candidate.payments)} installments totaling {format_decimal_compact(candidate.total_payable)}; the schedule stays above the minimum."
    if candidate.spending_changes:
        return f"Make spending change(s) {format_spending_changes(candidate.spending_changes)}, then pay safely."
    return "Use the verified plan; projected balances stay above the minimum."


def format_payment_plan(payments: Iterable[RequestPayment]) -> str:
    payment_tuple = tuple(payments)
    if not payment_tuple:
        return "none"
    return "|".join(f"{payment.date.isoformat()}:{_format_plan_amount(payment.amount)}" for payment in payment_tuple)


def format_spending_changes(changes: Iterable[SpendingChange]) -> str:
    change_tuple = tuple(changes)
    if not change_tuple:
        return "none"
    return "|".join(change.render() for change in change_tuple)


def _format_plan_amount(value: Decimal) -> str:
    if value == value.to_integral_value():
        return format_decimal_plain(value.quantize(Decimal("1")))
    return format_decimal_plain(value.quantize(Decimal("0.01")))


OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def row_to_csv_dict(row: DecisionRow) -> dict[str, str]:
    return {
        "request_id": row.request_id,
        "amount_safe_to_pay": format_decimal_compact(row.amount_safe_to_pay),
        "affordability_status": row.affordability_status.value,
        "recommended_payment_method": row.recommended_payment_method.value,
        "payment_plan": row.payment_plan,
        "earliest_date_for_full_payment": row.earliest_date_for_full_payment.isoformat() if row.earliest_date_for_full_payment else "",
        "spending_changes_needed": row.spending_changes_needed,
        "decision_explanation": row.decision_explanation,
    }
