from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .capacity import RequestPayment, plan_safety
from .enums import AffordabilityStatus, PaymentMethod
from .loaders import LoadedDataset
from .models import PaymentOption, Request
from .planner import DecisionRow, SpendingChange, add_calendar_months, apply_spending_changes
from .simulator import BaselineSimulation, simulate_baseline_for_request


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    errors: tuple[str, ...]


def verify_decision(
    dataset: LoadedDataset,
    request: Request,
    row: DecisionRow,
    *,
    baseline: BaselineSimulation | None = None,
) -> VerificationResult:
    baseline = baseline or simulate_baseline_for_request(dataset, request)
    errors: list[str] = []
    profile = dataset.profile_by_user[request.user_id]
    if row.request_id != request.request_id:
        errors.append("request_id_mismatch")
    if row.amount_safe_to_pay < Decimal("0") or row.amount_safe_to_pay > request.requested_amount:
        errors.append("amount_safe_out_of_bounds")
    if not row.decision_explanation:
        errors.append("empty_explanation")
    if row.recommended_payment_method is not PaymentMethod.NOT_RECOMMENDED:
        if row.recommended_payment_method not in profile.payment_methods_user_will_consider and row.recommended_payment_method is not PaymentMethod.WAIT:
            errors.append("method_not_preferred")
        if row.recommended_payment_method is PaymentMethod.WAIT and PaymentMethod.FULL_PAYMENT not in profile.payment_methods_user_will_consider:
            errors.append("wait_without_full_payment_preference")
    payments = _parse_payment_plan(row.payment_plan, errors)
    changes = _parse_spending_changes(row.spending_changes_needed, errors)
    _verify_spending_changes(dataset, request, changes, errors)
    adjusted = apply_spending_changes(baseline, changes, profile)
    if row.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED:
        if payments:
            errors.append("not_recommended_has_payments")
        if row.affordability_status is not AffordabilityStatus.NOT_AFFORDABLE:
            errors.append("not_recommended_status")
        return VerificationResult(valid=not errors, errors=tuple(errors))
    _verify_status_specific(request, row, payments, changes, errors)
    if not payments:
        errors.append("recommended_plan_missing_payments")
    if any(payment.date < request.request_date for payment in payments):
        errors.append("payment_before_request_date")
    if payments and max(payment.date for payment in payments) > request.desired_completion_date:
        errors.append("payment_after_deadline")
    _verify_method_specific(dataset, request, row, payments, errors)
    safety = plan_safety(adjusted, payments)
    if not safety.safe:
        errors.extend(safety.rejection_reasons)
    return VerificationResult(valid=not errors, errors=tuple(dict.fromkeys(errors)))


def _verify_status_specific(
    request: Request,
    row: DecisionRow,
    payments: tuple[RequestPayment, ...],
    changes: tuple[SpendingChange, ...],
    errors: list[str],
) -> None:
    if row.affordability_status is AffordabilityStatus.AFFORDABLE_NOW:
        if row.recommended_payment_method is not PaymentMethod.FULL_PAYMENT:
            errors.append("affordable_now_requires_full_payment")
        if changes:
            errors.append("affordable_now_has_spending_changes")
        if len(payments) != 1 or payments[0].date != request.request_date or payments[0].amount != request.requested_amount:
            errors.append("affordable_now_plan_mismatch")
    elif row.affordability_status is AffordabilityStatus.AFFORDABLE_LATER:
        if row.recommended_payment_method is not PaymentMethod.WAIT:
            errors.append("affordable_later_requires_wait")
        if len(payments) != 1 or payments[0].date <= request.request_date or payments[0].amount != request.requested_amount:
            errors.append("affordable_later_plan_mismatch")
    elif row.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN:
        if row.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED:
            errors.append("with_plan_not_recommended")
        if row.recommended_payment_method is PaymentMethod.WAIT:
            errors.append("with_plan_wait_method")
        if row.recommended_payment_method is PaymentMethod.FULL_PAYMENT and not changes:
            errors.append("with_plan_full_payment_requires_spending_changes")
        if not payments:
            errors.append("with_plan_missing_payments")
    elif row.affordability_status is AffordabilityStatus.NOT_AFFORDABLE:
        if row.recommended_payment_method is not PaymentMethod.NOT_RECOMMENDED:
            errors.append("not_affordable_requires_not_recommended")
        if payments:
            errors.append("not_affordable_has_payments")


def _verify_method_specific(
    dataset: LoadedDataset,
    request: Request,
    row: DecisionRow,
    payments: tuple[RequestPayment, ...],
    errors: list[str],
) -> None:
    total = sum((payment.amount for payment in payments), Decimal("0"))
    if row.recommended_payment_method is PaymentMethod.FULL_PAYMENT:
        if len(payments) != 1 or total != request.requested_amount:
            errors.append("invalid_full_payment")
    elif row.recommended_payment_method is PaymentMethod.WAIT:
        if len(payments) != 1 or total != request.requested_amount:
            errors.append("invalid_wait_payment")
        if payments and payments[0].date <= request.request_date:
            errors.append("wait_not_later")
    elif row.recommended_payment_method is PaymentMethod.PARTIAL_PAYMENT:
        if not request.allows_partial_payment:
            errors.append("partial_not_allowed")
        if row.amount_safe_to_pay <= Decimal("0") or row.amount_safe_to_pay >= request.requested_amount:
            errors.append("partial_safe_amount_out_of_range")
        if row.earliest_date_for_full_payment is None:
            errors.append("partial_missing_earliest_full_date")
        elif row.earliest_date_for_full_payment > request.desired_completion_date:
            errors.append("partial_earliest_after_deadline")
        if len(payments) != 2:
            errors.append("partial_payment_count")
        else:
            if payments[0].date != request.request_date:
                errors.append("partial_first_date")
            if payments[0].amount != row.amount_safe_to_pay:
                errors.append("partial_first_amount")
            if payments[1].date != row.earliest_date_for_full_payment:
                errors.append("partial_second_date")
            if payments[1].amount != request.requested_amount - row.amount_safe_to_pay:
                errors.append("partial_second_amount")
        if total != request.requested_amount:
            errors.append("partial_total")
    elif row.recommended_payment_method is PaymentMethod.INSTALLMENTS:
        option = _matching_installment_option(dataset, request, payments)
        if option is None:
            errors.append("installment_option_mismatch")
        else:
            profile = dataset.profile_by_user[request.user_id]
            if PaymentMethod.INSTALLMENTS not in profile.payment_methods_user_will_consider:
                errors.append("installments_not_preferred")
            if profile.max_installment_months is None:
                errors.append("installments_without_max_months")
            else:
                max_completion = add_calendar_months(option.first_payment_date, profile.max_installment_months)
                if payments and payments[-1].date > max_completion:
                    errors.append("installments_exceed_max_months")
            if len(payments) != option.number_of_payments:
                errors.append("installment_payment_count")
            if total != option.total_payable_amount:
                errors.append("installment_total_payable_mismatch")
    else:
        errors.append("unsupported_method")


def _matching_installment_option(
    dataset: LoadedDataset,
    request: Request,
    payments: tuple[RequestPayment, ...],
) -> PaymentOption | None:
    for option in dataset.payment_options_by_request.get(request.request_id, ()):
        if option.payment_method is not PaymentMethod.INSTALLMENTS:
            continue
        expected: list[RequestPayment] = []
        current = option.first_payment_date
        for idx in range(option.number_of_payments):
            expected.append(RequestPayment(current, option.payment_amount, option.payment_option_id))
            if idx < option.number_of_payments - 1:
                current = current.fromordinal(current.toordinal() + (option.payment_frequency_days or 0))
        if tuple((item.date, item.amount) for item in expected) == tuple((item.date, item.amount) for item in payments):
            return option
    return None


def _parse_payment_plan(text: str, errors: list[str]) -> tuple[RequestPayment, ...]:
    if text == "none":
        return ()
    payments: list[RequestPayment] = []
    for part in text.split("|"):
        try:
            date_text, amount_text = part.split(":", 1)
            payments.append(RequestPayment(date.fromisoformat(date_text), Decimal(amount_text)))
        except (ValueError, InvalidOperation):
            errors.append("invalid_payment_plan_syntax")
            return ()
    if payments != sorted(payments, key=lambda item: (item.date, item.amount)):
        errors.append("payment_plan_not_chronological")
    return tuple(payments)


def _parse_spending_changes(text: str, errors: list[str]) -> tuple[SpendingChange, ...]:
    if text == "none":
        return ()
    changes: list[SpendingChange] = []
    for part in text.split("|"):
        bits = part.split(":")
        try:
            if len(bits) == 2 and bits[0] == "stop":
                changes.append(SpendingChange("stop", bits[1]))
            elif len(bits) == 3 and bits[0] == "reduce_to":
                changes.append(SpendingChange("reduce_to", bits[1], Decimal(bits[2])))
            else:
                errors.append("invalid_spending_syntax")
        except InvalidOperation:
            errors.append("invalid_spending_amount")
    return tuple(changes)


def _verify_spending_changes(
    dataset: LoadedDataset,
    request: Request,
    changes: tuple[SpendingChange, ...],
    errors: list[str],
) -> None:
    if len(changes) > 3:
        errors.append("too_many_spending_changes")
    profile = dataset.profile_by_user[request.user_id]
    protected = set(profile.expense_categories_to_protect)
    reduce_allowed = set(profile.expense_categories_user_is_willing_to_reduce)
    stop_allowed = set(profile.expense_categories_user_is_willing_to_stop)
    seen: dict[str, str] = {}
    for change in changes:
        previous = seen.get(change.event_id)
        if previous is not None and previous != change.action:
            errors.append("conflicting_spending_changes")
        seen[change.event_id] = change.action
        event = dataset.event_by_id.get(change.event_id)
        if event is None:
            errors.append("unknown_spending_event")
            continue
        if event.user_id != request.user_id:
            errors.append("spending_event_wrong_user")
        if event.category in protected:
            errors.append("protected_category_changed")
        if change.action == "stop":
            if event.category not in stop_allowed or event.flexibility not in {"stoppable", "reducible_or_stoppable"}:
                errors.append("illegal_stop")
        elif change.action == "reduce_to":
            if event.category not in reduce_allowed or event.flexibility not in {"reducible", "reducible_or_stoppable"}:
                errors.append("illegal_reduce")
            floor = event.minimum_allowed_amount if isinstance(event.minimum_allowed_amount, Decimal) else Decimal("0")
            if change.new_amount is None or change.new_amount < floor:
                errors.append("below_minimum_allowed_amount")
        else:
            errors.append("unknown_spending_action")
