from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Iterable

from .enums import Direction
from .lifecycle import EffectiveEvent, EffectiveEventClass, LifecycleResolutionStatus, resolve_lifecycles
from .models import FinancialEvent
from .snapshot import RequestSnapshot


class CadenceType(str, Enum):
    MONTHLY = "monthly"
    FIXED_DAYS = "fixed_days"


class AmountBehavior(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"


@dataclass(frozen=True)
class RecurrencePolicy:
    min_occurrences: int = 3
    historical_cutoff: str = "settlement_date_or_event_date_on_or_before_request_date"


@dataclass(frozen=True)
class StreamKey:
    user_id: str
    direction: Direction
    category: str
    event_type: str
    normalized_description: str
    currency: str

    def as_id_part(self) -> str:
        raw = "|".join(
            [
                self.user_id,
                self.direction.value,
                self.category,
                self.event_type,
                self.normalized_description,
                self.currency,
            ]
        )
        return re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("_")


@dataclass(frozen=True)
class RecurringStream:
    stream_id: str
    stream_key: StreamKey
    cadence: CadenceType
    cadence_interval_days: int | None
    calendar_day: int | None
    month_end: bool
    historical_dates: tuple[date, ...]
    historical_amounts: tuple[Decimal, ...]
    supporting_event_ids: tuple[str, ...]
    latest_known_occurrence: date
    confidence: str
    evidence_strength: int
    flexibility: str | None
    minimum_allowed_amount: Decimal | None
    amount_behavior: AmountBehavior
    termination_event_ids: tuple[str, ...] = ()
    amendment_event_ids: tuple[str, ...] = ()

    @property
    def user_id(self) -> str:
        return self.stream_key.user_id

    @property
    def direction(self) -> Direction:
        return self.stream_key.direction

    @property
    def category(self) -> str:
        return self.stream_key.category

    @property
    def event_type(self) -> str:
        return self.stream_key.event_type

    @property
    def normalized_description(self) -> str:
        return self.stream_key.normalized_description

    @property
    def currency(self) -> str:
        return self.stream_key.currency


def infer_streams_for_snapshot(
    snapshot: RequestSnapshot,
    raw_event_by_id: dict[str, FinancialEvent],
    *,
    policy: RecurrencePolicy = RecurrencePolicy(),
) -> tuple[RecurringStream, ...]:
    result = resolve_lifecycles(
        snapshot.user_events,
        request_date=snapshot.request.request_date,
        messages=tuple(message.message for message in snapshot.messages),
    )
    return infer_recurring_streams(result.effective_events, raw_event_by_id, policy=policy)


def infer_recurring_streams(
    effective_events: Iterable[EffectiveEvent],
    raw_event_by_id: dict[str, FinancialEvent],
    *,
    policy: RecurrencePolicy = RecurrencePolicy(),
) -> tuple[RecurringStream, ...]:
    groups: dict[StreamKey, list[EffectiveEvent]] = defaultdict(list)
    for event in effective_events:
        if not _eligible_for_recurrence(event):
            continue
        key = stream_key_for_effective_event(event, raw_event_by_id)
        groups[key].append(event)

    streams: list[RecurringStream] = []
    for key, events in groups.items():
        ordered = tuple(sorted(events, key=lambda item: (_effective_date(item), item.effective_event_id)))
        if len(ordered) < policy.min_occurrences:
            continue
        cadence = _infer_cadence(tuple(_effective_date(event) for event in ordered))
        if cadence is None:
            continue
        amounts = tuple(event.amount for event in ordered)
        if any(not isinstance(amount, Decimal) for amount in amounts):
            continue
        source_ids = tuple(event.source_event_ids[-1] for event in ordered)
        flexibilities = tuple(event.flexibility for event in ordered if event.flexibility)
        minimums = tuple(event.minimum_allowed_amount for event in ordered if isinstance(event.minimum_allowed_amount, Decimal))
        streams.append(
            RecurringStream(
                stream_id="stream:" + key.as_id_part(),
                stream_key=key,
                cadence=cadence[0],
                cadence_interval_days=cadence[1],
                calendar_day=cadence[2],
                month_end=cadence[3],
                historical_dates=tuple(_effective_date(event) for event in ordered),
                historical_amounts=amounts,  # type: ignore[arg-type]
                supporting_event_ids=source_ids,
                latest_known_occurrence=_effective_date(ordered[-1]),
                confidence=_confidence(len(ordered), cadence[0]),
                evidence_strength=len(ordered),
                flexibility=flexibilities[-1] if flexibilities else None,
                minimum_allowed_amount=minimums[-1] if minimums else None,
                amount_behavior=AmountBehavior.FIXED if len(set(amounts)) == 1 else AmountBehavior.VARIABLE,
            )
        )
    return tuple(sorted(streams, key=lambda item: item.stream_id))


def stream_key_for_effective_event(event: EffectiveEvent, raw_event_by_id: dict[str, FinancialEvent]) -> StreamKey:
    raw = raw_event_by_id[event.source_event_ids[-1]]
    return StreamKey(
        user_id=event.user_id,
        direction=event.direction,
        category=event.category,
        event_type=event.event_type,
        normalized_description=normalize_description(raw.description),
        currency=event.currency,
    )


def normalize_description(description: str) -> str:
    text = description.lower().strip()
    text = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", text)
    text = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", " ", text)
    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}\b", " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def next_occurrence_after(stream: RecurringStream, after_date: date, *, include_after: bool = False) -> date:
    candidate = stream.latest_known_occurrence
    while candidate < after_date or (candidate == after_date and not include_after):
        candidate = _advance_stream_date(stream, candidate)
    return candidate


def generate_occurrences(stream: RecurringStream, start_date: date, end_date: date) -> tuple[date, ...]:
    dates: list[date] = []
    current = next_occurrence_after(stream, start_date, include_after=True)
    while current <= end_date:
        dates.append(current)
        current = _advance_stream_date(stream, current)
    return tuple(dates)


def event_matches_stream_identity(
    stream: RecurringStream,
    event: EffectiveEvent,
    raw_event_by_id: dict[str, FinancialEvent],
) -> bool:
    return stream.stream_key == stream_key_for_effective_event(event, raw_event_by_id)


def _eligible_for_recurrence(event: EffectiveEvent) -> bool:
    return (
        event.event_class is EffectiveEventClass.HISTORICAL_EVIDENCE
        and event.resolution_status is LifecycleResolutionStatus.RESOLVED
        and event.amount is not None
        and event.direction in (Direction.CREDIT, Direction.DEBIT)
    )


def _effective_date(event: EffectiveEvent) -> date:
    return event.settlement_date or event.event_date


def _infer_cadence(dates: tuple[date, ...]) -> tuple[CadenceType, int | None, int | None, bool] | None:
    ordered = tuple(sorted(dates))
    intervals = tuple((later - earlier).days for earlier, later in zip(ordered, ordered[1:]))
    if len(intervals) < 2:
        return None
    if _is_monthly(ordered, intervals):
        if all(_is_month_end(item) for item in ordered):
            return (CadenceType.MONTHLY, None, None, True)
        return (CadenceType.MONTHLY, None, ordered[-1].day, False)
    fixed = _fixed_interval_days(intervals)
    if fixed is not None:
        return (CadenceType.FIXED_DAYS, fixed, None, False)
    return None


def _is_monthly(dates: tuple[date, ...], intervals: tuple[int, ...]) -> bool:
    if not all(25 <= interval <= 35 for interval in intervals):
        return False
    if len({item.day for item in dates}) == 1:
        return True
    return all(_is_month_end(item) for item in dates)


def _fixed_interval_days(intervals: tuple[int, ...]) -> int | None:
    if len(set(intervals)) == 1:
        return intervals[0]
    for target in (7, 14, 28):
        if all(abs(interval - target) <= 1 for interval in intervals):
            return target
    return None


def _advance_stream_date(stream: RecurringStream, current: date) -> date:
    if stream.cadence is CadenceType.FIXED_DAYS:
        if stream.cadence_interval_days is None:
            raise ValueError(f"Stream {stream.stream_id} is missing fixed-day interval")
        return current + timedelta(days=stream.cadence_interval_days)
    if stream.cadence is CadenceType.MONTHLY:
        return _add_one_month(current, calendar_day=stream.calendar_day, month_end=stream.month_end)
    raise ValueError(f"Unsupported cadence {stream.cadence}")


def _add_one_month(current: date, *, calendar_day: int | None, month_end: bool) -> date:
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    last_day = calendar.monthrange(year, month)[1]
    if month_end:
        day = last_day
    else:
        if calendar_day is None:
            raise ValueError("calendar_day is required for non-month-end monthly cadence")
        day = min(calendar_day, last_day)
    return date(year, month, day)


def _is_month_end(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _confidence(count: int, cadence: CadenceType) -> str:
    if cadence is CadenceType.MONTHLY and count >= 4:
        return "high"
    if count >= 5:
        return "high"
    return "medium"
