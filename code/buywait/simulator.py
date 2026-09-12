from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import calendar
from collections import defaultdict
from typing import Callable, Iterable

from .enums import Direction
from .errors import IntegrityError
from .evidence import (
    MessageFact,
    apply_message_facts_to_effective_events,
    apply_message_facts_to_streams,
    extract_message_facts,
    ResolvedImageAmount,
    resolve_image_backed_event_amounts,
)
from .lifecycle import EffectiveEvent, EffectiveEventClass, LifecycleResolutionStatus, resolve_lifecycles
from .loaders import LoadedDataset
from .models import FinancialEvent, FinancialProfile, Request
from .recurrence import (
    AmountBehavior,
    RecurringStream,
    event_matches_stream_identity,
    generate_occurrences,
    infer_recurring_streams,
)


FxLookup = Callable[[date, str, str], Decimal]


@dataclass(frozen=True)
class SimulationPolicy:
    horizon_days: int = 90
    include_horizon_end: bool = True
    same_day_policy: str = "aggregate_daily_closing_balance"
    pending_debit_policy: str = "reserve_on_earliest_known_date_not_before_request"
    variable_debit_policy: str = "max_historical_amount"
    variable_credit_policy: str = "min_historical_amount"


@dataclass(frozen=True)
class LedgerOccurrence:
    date: date
    direction: Direction
    amount: Decimal
    currency: str
    home_currency_amount: Decimal
    source_type: str
    source_ids: tuple[str, ...]
    category: str
    explicit: bool
    reason_included: str
    recurrence_stream_id: str | None = None


@dataclass(frozen=True)
class DailyBalance:
    date: date
    opening_balance: Decimal
    credits: Decimal
    debits: Decimal
    closing_balance: Decimal
    minimum_balance_to_keep: Decimal
    headroom: Decimal
    occurrences: tuple[LedgerOccurrence, ...]


@dataclass(frozen=True)
class BaselineSimulation:
    request_id: str
    user_id: str
    request_date: date
    horizon_end: date
    opening_balance_anchor: Decimal
    days: tuple[DailyBalance, ...]
    occurrences: tuple[LedgerOccurrence, ...]
    suffix_min_headroom: dict[date, Decimal]
    lifecycle_unresolved_count: int
    explicit_future_event_count: int
    inferred_recurring_occurrence_count: int
    deduplicated_recurring_occurrence_count: int
    skipped_missing_amount_event_count: int
    skipped_missing_amount_event_ids: tuple[str, ...]
    resolved_image_amounts: tuple[ResolvedImageAmount, ...]
    message_facts_applied: tuple[MessageFact, ...]
    fx_conversion_count: int

    @property
    def minimum_projected_balance(self) -> Decimal:
        return min(day.closing_balance for day in self.days)

    @property
    def minimum_projected_balance_date(self) -> date:
        return min(self.days, key=lambda day: (day.closing_balance, day.date)).date

    @property
    def minimum_headroom(self) -> Decimal:
        return min(day.headroom for day in self.days)

    @property
    def baseline_safe(self) -> bool:
        return self.minimum_headroom >= Decimal("0")


def simulate_baseline_for_request(
    dataset: LoadedDataset,
    request: Request,
    *,
    policy: SimulationPolicy = SimulationPolicy(),
) -> BaselineSimulation:
    profile = dataset.profile_by_user[request.user_id]
    events, resolved_image_amounts = resolve_image_backed_event_amounts(
        dataset,
        dataset.events_by_user.get(request.user_id, ()),
    )
    messages = dataset.messages_by_user.get(request.user_id, ())
    filtered_messages = tuple(message for message in messages if message.sent_at.date() <= request.request_date)
    return simulate_baseline_from_events(
        request_id=request.request_id,
        request_date=request.request_date,
        profile=profile,
        events=events,
        raw_event_by_id=dataset.event_by_id,
        fx_lookup=lambda rate_date, source_currency, home_currency: dataset.require_exchange_rate(
            rate_date, source_currency, home_currency
        ).rate,
        messages=filtered_messages,
        resolved_image_amounts=resolved_image_amounts,
        policy=policy,
    )


def simulate_baseline_from_events(
    *,
    request_id: str,
    request_date: date,
    profile: FinancialProfile,
    events: Iterable[FinancialEvent],
    raw_event_by_id: dict[str, FinancialEvent],
    fx_lookup: FxLookup,
    messages=(),
    resolved_image_amounts: tuple[ResolvedImageAmount, ...] = (),
    message_facts: tuple[MessageFact, ...] | None = None,
    policy: SimulationPolicy = SimulationPolicy(),
) -> BaselineSimulation:
    event_tuple = tuple(events)
    lifecycle = resolve_lifecycles(event_tuple, request_date=request_date, messages=messages)
    fact_tuple = message_facts if message_facts is not None else extract_message_facts(messages, request_date=request_date)
    effective_events = apply_message_facts_to_effective_events(lifecycle.effective_events, fact_tuple)
    streams = apply_message_facts_to_streams(
        infer_recurring_streams(effective_events, raw_event_by_id),
        fact_tuple,
    )
    return simulate_baseline_from_effective_events(
        request_id=request_id,
        request_date=request_date,
        profile=profile,
        effective_events=effective_events,
        streams=streams,
        raw_event_by_id=raw_event_by_id,
        fx_lookup=fx_lookup,
        lifecycle_unresolved_count=len(lifecycle.unresolved_components),
        resolved_image_amounts=resolved_image_amounts,
        message_facts_applied=fact_tuple,
        policy=policy,
    )


def simulate_baseline_from_effective_events(
    *,
    request_id: str,
    request_date: date,
    profile: FinancialProfile,
    effective_events: Iterable[EffectiveEvent],
    streams: Iterable[RecurringStream],
    raw_event_by_id: dict[str, FinancialEvent],
    fx_lookup: FxLookup,
    lifecycle_unresolved_count: int = 0,
    resolved_image_amounts: tuple[ResolvedImageAmount, ...] = (),
    message_facts_applied: tuple[MessageFact, ...] = (),
    policy: SimulationPolicy = SimulationPolicy(),
) -> BaselineSimulation:
    horizon_end = _horizon_end(request_date, policy)
    effective_tuple = tuple(effective_events)
    stream_tuple = tuple(streams)
    explicit, skipped_missing_amount_event_ids = _explicit_future_occurrences(
        effective_tuple,
        request_date=request_date,
        horizon_end=horizon_end,
        profile=profile,
        fx_lookup=fx_lookup,
        policy=policy,
    )
    inferred, deduped = _inferred_recurring_occurrences(
        stream_tuple,
        explicit_events=tuple(event for event in effective_tuple if _explicit_event_included(event, request_date, horizon_end)),
        raw_event_by_id=raw_event_by_id,
        request_date=request_date,
        horizon_end=horizon_end,
        profile=profile,
        fx_lookup=fx_lookup,
        policy=policy,
    )
    variable_essential = _variable_essential_occurrences(
        effective_tuple,
        streams=stream_tuple,
        existing_occurrences=explicit + inferred,
        request_date=request_date,
        horizon_end=horizon_end,
        profile=profile,
        fx_lookup=fx_lookup,
    )
    occurrences = tuple(sorted(explicit + inferred + variable_essential, key=_occurrence_sort_key))
    days = _simulate_days(request_id, request_date, horizon_end, profile, occurrences)
    return BaselineSimulation(
        request_id=request_id,
        user_id=profile.user_id,
        request_date=request_date,
        horizon_end=horizon_end,
        opening_balance_anchor=profile.current_available_balance,
        days=days,
        occurrences=occurrences,
        suffix_min_headroom=_suffix_min_headroom(days),
        lifecycle_unresolved_count=lifecycle_unresolved_count,
        explicit_future_event_count=len(explicit),
        inferred_recurring_occurrence_count=len(inferred) + len(variable_essential),
        deduplicated_recurring_occurrence_count=deduped,
        skipped_missing_amount_event_count=len(skipped_missing_amount_event_ids),
        skipped_missing_amount_event_ids=skipped_missing_amount_event_ids,
        resolved_image_amounts=resolved_image_amounts,
        message_facts_applied=message_facts_applied,
        fx_conversion_count=sum(1 for occurrence in occurrences if occurrence.currency != profile.home_currency),
    )


def _horizon_end(request_date: date, policy: SimulationPolicy) -> date:
    if policy.include_horizon_end:
        return request_date + timedelta(days=policy.horizon_days)
    return request_date + timedelta(days=policy.horizon_days - 1)


def _explicit_future_occurrences(
    events: tuple[EffectiveEvent, ...],
    *,
    request_date: date,
    horizon_end: date,
    profile: FinancialProfile,
    fx_lookup: FxLookup,
    policy: SimulationPolicy,
) -> tuple[tuple[LedgerOccurrence, ...], tuple[str, ...]]:
    occurrences: list[LedgerOccurrence] = []
    skipped_missing_amount_event_ids: list[str] = []
    for event in events:
        if not _explicit_event_included(event, request_date, horizon_end):
            continue
        occurrence_date = _explicit_occurrence_date(event, request_date, policy)
        if occurrence_date < request_date or occurrence_date > horizon_end:
            continue
        if not isinstance(event.amount, Decimal):
            skipped_missing_amount_event_ids.extend(event.source_event_ids)
            continue
        occurrences.append(
            LedgerOccurrence(
                date=occurrence_date,
                direction=event.direction,
                amount=event.amount,
                currency=event.currency,
                home_currency_amount=_to_home_currency(
                    event.amount,
                    event.currency,
                    profile.home_currency,
                    occurrence_date,
                    fx_lookup,
                ),
                source_type="explicit_event",
                source_ids=event.source_event_ids,
                category=event.category,
                explicit=True,
                reason_included=event.event_class.value,
            )
        )
    return (tuple(occurrences), tuple(skipped_missing_amount_event_ids))


def _inferred_recurring_occurrences(
    streams: tuple[RecurringStream, ...],
    *,
    explicit_events: tuple[EffectiveEvent, ...],
    raw_event_by_id: dict[str, FinancialEvent],
    request_date: date,
    horizon_end: date,
    profile: FinancialProfile,
    fx_lookup: FxLookup,
    policy: SimulationPolicy,
) -> tuple[tuple[LedgerOccurrence, ...], int]:
    dedupe_keys = _explicit_dedupe_keys(streams, explicit_events, raw_event_by_id, request_date, policy)
    occurrences: list[LedgerOccurrence] = []
    deduped = 0
    for stream in sorted(streams, key=lambda item: item.stream_id):
        if stream.termination_event_ids:
            continue
        amount = _stream_forecast_amount(stream, policy)
        for occurrence_date in generate_occurrences(stream, request_date, horizon_end):
            if _suppressed_by_confirmed_income(stream, occurrence_date, explicit_events, raw_event_by_id, request_date, policy):
                deduped += 1
                continue
            if (stream.stream_id, occurrence_date) in dedupe_keys:
                deduped += 1
                continue
            occurrences.append(
                LedgerOccurrence(
                    date=occurrence_date,
                    direction=stream.direction,
                    amount=amount,
                    currency=stream.currency,
                    home_currency_amount=_to_home_currency(
                        amount,
                        stream.currency,
                        profile.home_currency,
                        occurrence_date,
                        fx_lookup,
                    ),
                    source_type="inferred_recurrence",
                    source_ids=stream.supporting_event_ids,
                    category=stream.category,
                    explicit=False,
                    reason_included="recurrence_stream_projection",
                    recurrence_stream_id=stream.stream_id,
                )
            )
    return (tuple(occurrences), deduped)


def _explicit_event_included(event: EffectiveEvent, request_date: date, horizon_end: date) -> bool:
    if event.resolution_status is not LifecycleResolutionStatus.RESOLVED:
        return False
    if event.event_class in (
        EffectiveEventClass.FUTURE_CONFIRMED_CREDIT,
        EffectiveEventClass.FUTURE_CONFIRMED_DEBIT,
        EffectiveEventClass.FUTURE_SCHEDULED_CREDIT,
        EffectiveEventClass.FUTURE_SCHEDULED_DEBIT,
        EffectiveEventClass.PENDING_DEBIT,
    ):
        effective_date = event.settlement_date or event.event_date
        return effective_date <= horizon_end and (effective_date >= request_date or event.event_class is EffectiveEventClass.PENDING_DEBIT)
    return False


def _explicit_occurrence_date(event: EffectiveEvent, request_date: date, policy: SimulationPolicy) -> date:
    if event.event_class is EffectiveEventClass.PENDING_DEBIT:
        candidates = tuple(item for item in (event.event_date, event.settlement_date) if isinstance(item, date))
        if not candidates:
            return request_date
        return max(request_date, min(candidates))
    effective_date = event.settlement_date or event.event_date
    if not isinstance(effective_date, date):
        raise IntegrityError(f"Effective event {event.effective_event_id} is missing an effective date")
    return effective_date


def _explicit_dedupe_keys(
    streams: tuple[RecurringStream, ...],
    explicit_events: tuple[EffectiveEvent, ...],
    raw_event_by_id: dict[str, FinancialEvent],
    request_date: date,
    policy: SimulationPolicy,
) -> set[tuple[str, date]]:
    keys: set[tuple[str, date]] = set()
    for event in explicit_events:
        occurrence_date = _explicit_occurrence_date(event, request_date, policy)
        for stream in streams:
            if _event_matches_stream_for_dedupe(stream, event, raw_event_by_id, policy):
                keys.add((stream.stream_id, occurrence_date))
    return keys


def _event_matches_stream_for_dedupe(
    stream: RecurringStream,
    event: EffectiveEvent,
    raw_event_by_id: dict[str, FinancialEvent],
    policy: SimulationPolicy,
) -> bool:
    if event_matches_stream_identity(stream, event, raw_event_by_id):
        return True
    if stream.direction is Direction.CREDIT and stream.category == "salary":
        return (
            _event_matches_stream_cash_identity(stream, event)
            and isinstance(event.amount, Decimal)
            and event.amount == _stream_forecast_amount(stream, policy)
        )
    return _event_matches_stream_cash_identity(stream, event)


def _event_matches_stream_cash_identity(stream: RecurringStream, event: EffectiveEvent) -> bool:
    return (
        stream.user_id == event.user_id
        and stream.direction is event.direction
        and stream.category == event.category
        and stream.event_type == event.event_type
        and stream.currency == event.currency
    )


def _suppressed_by_confirmed_income(
    stream: RecurringStream,
    occurrence_date: date,
    explicit_events: tuple[EffectiveEvent, ...],
    raw_event_by_id: dict[str, FinancialEvent],
    request_date: date,
    policy: SimulationPolicy,
) -> bool:
    if stream.direction is not Direction.CREDIT or stream.category != "salary":
        return False
    for event in explicit_events:
        if event.direction is not Direction.CREDIT or event.category != "salary":
            continue
        if not _event_matches_stream_for_dedupe(stream, event, raw_event_by_id, policy):
            continue
        explicit_date = _explicit_occurrence_date(event, request_date, policy)
        if request_date <= occurrence_date < explicit_date:
            return True
    return False


def _stream_forecast_amount(stream: RecurringStream, policy: SimulationPolicy) -> Decimal:
    if stream.amount_behavior is AmountBehavior.FIXED:
        return stream.historical_amounts[-1]
    if stream.direction is Direction.DEBIT:
        return max(stream.historical_amounts)
    if stream.direction is Direction.CREDIT:
        return min(stream.historical_amounts)
    raise IntegrityError(f"Cannot forecast non-cash stream {stream.stream_id}")


def _variable_essential_occurrences(
    events: tuple[EffectiveEvent, ...],
    *,
    streams: tuple[RecurringStream, ...],
    existing_occurrences: tuple[LedgerOccurrence, ...],
    request_date: date,
    horizon_end: date,
    profile: FinancialProfile,
    fx_lookup: FxLookup,
) -> tuple[LedgerOccurrence, ...]:
    categories = set(profile.expense_categories_to_protect) | {"groceries", "transport"}
    represented_event_ids = {
        source_id
        for stream in streams
        if stream.direction is Direction.DEBIT
        for source_id in stream.supporting_event_ids
    }
    recent_months = _recent_complete_months(request_date, count=3)
    by_category: dict[str, list[EffectiveEvent]] = defaultdict(list)
    for event in sorted(events, key=lambda item: (_effective_event_date(item), item.effective_event_id)):
        if event.event_class is not EffectiveEventClass.HISTORICAL_EVIDENCE:
            continue
        if event.resolution_status is not LifecycleResolutionStatus.RESOLVED:
            continue
        if event.direction is not Direction.DEBIT or event.category not in categories:
            continue
        if not isinstance(event.amount, Decimal):
            continue
        effective_date = _effective_event_date(event)
        if effective_date >= request_date or _year_month(effective_date) not in recent_months:
            continue
        if any(source_id in represented_event_ids for source_id in event.source_event_ids):
            continue
        by_category[event.category].append(event)

    forecast_end = min(horizon_end, _next_income_date(existing_occurrences, request_date, horizon_end))
    occurrences: list[LedgerOccurrence] = []
    explicit_future_keys = {
        (occurrence.date, occurrence.category)
        for occurrence in existing_occurrences
        if occurrence.explicit and occurrence.direction is Direction.DEBIT
    }
    for category, category_events in sorted(by_category.items()):
        budget = _conservative_recent_month_budget(category_events, recent_months)
        base_events = [
            event
            for event in category_events
            if _year_month(_effective_event_date(event)) == recent_months[-1]
        ]
        base_total = sum((event.amount for event in base_events if isinstance(event.amount, Decimal)), Decimal("0"))
        if budget <= Decimal("0") or base_total <= Decimal("0"):
            continue
        scale = budget / base_total
        for year, month in _forecast_months(request_date, forecast_end):
            last_day = calendar.monthrange(year, month)[1]
            for event in base_events:
                effective_date = _effective_event_date(event)
                occurrence_date = date(year, month, min(effective_date.day, last_day))
                if occurrence_date < request_date or occurrence_date > forecast_end:
                    continue
                if (occurrence_date, category) in explicit_future_keys:
                    continue
                amount = (event.amount * scale).quantize(Decimal("0.01"))  # type: ignore[operator]
                occurrences.append(
                    LedgerOccurrence(
                        date=occurrence_date,
                        direction=Direction.DEBIT,
                        amount=amount,
                        currency=event.currency,
                        home_currency_amount=_to_home_currency(
                            amount,
                            event.currency,
                            profile.home_currency,
                            occurrence_date,
                            fx_lookup,
                        ),
                        source_type="variable_essential_forecast",
                        source_ids=(event.source_event_ids[-1],),
                        category=category,
                        explicit=False,
                        reason_included="one_cycle_max_recent_3_month_variable_essential",
                    )
                )
    return tuple(sorted(occurrences, key=_occurrence_sort_key))


def _effective_event_date(event: EffectiveEvent) -> date:
    effective_date = event.settlement_date or event.event_date
    if not isinstance(effective_date, date):
        raise IntegrityError(f"Effective event {event.effective_event_id} is missing a date")
    return effective_date


def _recent_complete_months(request_date: date, *, count: int) -> tuple[tuple[int, int], ...]:
    year = request_date.year
    month = request_date.month
    months: list[tuple[int, int]] = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year -= 1
            month = 12
        months.append((year, month))
    return tuple(reversed(months))


def _year_month(value: date) -> tuple[int, int]:
    return (value.year, value.month)


def _conservative_recent_month_budget(
    events: list[EffectiveEvent],
    recent_months: tuple[tuple[int, int], ...],
) -> Decimal:
    monthly: dict[tuple[int, int], Decimal] = {month: Decimal("0") for month in recent_months}
    for event in events:
        effective_date = _effective_event_date(event)
        key = _year_month(effective_date)
        if key in monthly and isinstance(event.amount, Decimal):
            monthly[key] += event.amount
    return max(monthly.values()) if monthly else Decimal("0")


def _forecast_months(start: date, end: date) -> tuple[tuple[int, int], ...]:
    current = date(start.year, start.month, 1)
    months: list[tuple[int, int]] = []
    while current <= end:
        months.append((current.year, current.month))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return tuple(months)


def _next_income_date(
    occurrences: tuple[LedgerOccurrence, ...],
    request_date: date,
    horizon_end: date,
) -> date:
    income_dates = [
        occurrence.date
        for occurrence in occurrences
        if occurrence.direction is Direction.CREDIT
        and occurrence.date >= request_date
        and occurrence.category in {"salary", "invoice", "windfall"}
    ]
    if income_dates:
        return min(income_dates)
    return min(horizon_end, request_date + timedelta(days=30))


def _to_home_currency(
    amount: Decimal,
    currency: str,
    home_currency: str,
    rate_date: date,
    fx_lookup: FxLookup,
) -> Decimal:
    if currency == home_currency:
        return amount
    return amount * fx_lookup(rate_date, currency, home_currency)


def _simulate_days(
    request_id: str,
    request_date: date,
    horizon_end: date,
    profile: FinancialProfile,
    occurrences: tuple[LedgerOccurrence, ...],
) -> tuple[DailyBalance, ...]:
    by_date: dict[date, list[LedgerOccurrence]] = {}
    for occurrence in occurrences:
        by_date.setdefault(occurrence.date, []).append(occurrence)

    days: list[DailyBalance] = []
    balance = profile.current_available_balance
    current = request_date
    while current <= horizon_end:
        day_occurrences = tuple(sorted(by_date.get(current, ()), key=_occurrence_sort_key))
        credits = sum(
            (occurrence.home_currency_amount for occurrence in day_occurrences if occurrence.direction is Direction.CREDIT),
            Decimal("0"),
        )
        debits = sum(
            (occurrence.home_currency_amount for occurrence in day_occurrences if occurrence.direction is Direction.DEBIT),
            Decimal("0"),
        )
        closing = balance + credits - debits
        days.append(
            DailyBalance(
                date=current,
                opening_balance=balance,
                credits=credits,
                debits=debits,
                closing_balance=closing,
                minimum_balance_to_keep=profile.minimum_balance_to_keep,
                headroom=closing - profile.minimum_balance_to_keep,
                occurrences=day_occurrences,
            )
        )
        balance = closing
        current += timedelta(days=1)

    if not days:
        raise IntegrityError(f"Simulation produced no daily balances for {request_id}")
    return tuple(days)


def _suffix_min_headroom(days: tuple[DailyBalance, ...]) -> dict[date, Decimal]:
    suffix: dict[date, Decimal] = {}
    running: Decimal | None = None
    for day in reversed(days):
        running = day.headroom if running is None else min(day.headroom, running)
        suffix[day.date] = running
    return suffix


def _occurrence_sort_key(occurrence: LedgerOccurrence) -> tuple[object, ...]:
    return (
        occurrence.date,
        occurrence.direction.value,
        occurrence.source_type,
        occurrence.recurrence_stream_id or "",
        occurrence.source_ids,
        occurrence.amount,
    )
