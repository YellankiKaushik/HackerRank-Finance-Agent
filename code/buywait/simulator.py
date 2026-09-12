from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Iterable

from .enums import Direction
from .errors import IntegrityError
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
    events = dataset.events_by_user.get(request.user_id, ())
    messages = dataset.messages_by_user.get(request.user_id, ())
    return simulate_baseline_from_events(
        request_id=request.request_id,
        request_date=request.request_date,
        profile=profile,
        events=events,
        raw_event_by_id=dataset.event_by_id,
        fx_lookup=lambda rate_date, source_currency, home_currency: dataset.require_exchange_rate(
            rate_date, source_currency, home_currency
        ).rate,
        messages=tuple(message for message in messages if message.sent_at.date() <= request.request_date),
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
    policy: SimulationPolicy = SimulationPolicy(),
) -> BaselineSimulation:
    event_tuple = tuple(events)
    lifecycle = resolve_lifecycles(event_tuple, request_date=request_date, messages=messages)
    streams = infer_recurring_streams(lifecycle.effective_events, raw_event_by_id)
    return simulate_baseline_from_effective_events(
        request_id=request_id,
        request_date=request_date,
        profile=profile,
        effective_events=lifecycle.effective_events,
        streams=streams,
        raw_event_by_id=raw_event_by_id,
        fx_lookup=fx_lookup,
        lifecycle_unresolved_count=len(lifecycle.unresolved_components),
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
    policy: SimulationPolicy = SimulationPolicy(),
) -> BaselineSimulation:
    horizon_end = _horizon_end(request_date, policy)
    effective_tuple = tuple(effective_events)
    stream_tuple = tuple(streams)
    explicit, skipped_missing_amount = _explicit_future_occurrences(
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
    occurrences = tuple(sorted(explicit + inferred, key=_occurrence_sort_key))
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
        inferred_recurring_occurrence_count=len(inferred),
        deduplicated_recurring_occurrence_count=deduped,
        skipped_missing_amount_event_count=skipped_missing_amount,
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
) -> tuple[tuple[LedgerOccurrence, ...], int]:
    occurrences: list[LedgerOccurrence] = []
    skipped_missing_amount = 0
    for event in events:
        if not _explicit_event_included(event, request_date, horizon_end):
            continue
        occurrence_date = _explicit_occurrence_date(event, request_date, policy)
        if occurrence_date < request_date or occurrence_date > horizon_end:
            continue
        if not isinstance(event.amount, Decimal):
            skipped_missing_amount += 1
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
    return (tuple(occurrences), skipped_missing_amount)


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
        amount = _stream_forecast_amount(stream, policy)
        for occurrence_date in generate_occurrences(stream, request_date, horizon_end):
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
            if event_matches_stream_identity(stream, event, raw_event_by_id):
                keys.add((stream.stream_id, occurrence_date))
    return keys


def _stream_forecast_amount(stream: RecurringStream, policy: SimulationPolicy) -> Decimal:
    if stream.amount_behavior is AmountBehavior.FIXED:
        return stream.historical_amounts[-1]
    if stream.direction is Direction.DEBIT:
        return max(stream.historical_amounts)
    if stream.direction is Direction.CREDIT:
        return min(stream.historical_amounts)
    raise IntegrityError(f"Cannot forecast non-cash stream {stream.stream_id}")


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
