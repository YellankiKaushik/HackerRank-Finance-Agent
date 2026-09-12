from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .enums import Direction, EventStatus
from .models import FinancialEvent, MessageRecord


class EffectiveEventClass(str, Enum):
    HISTORICAL_EVIDENCE = "historical_evidence"
    FUTURE_CONFIRMED_CREDIT = "future_confirmed_credit"
    FUTURE_CONFIRMED_DEBIT = "future_confirmed_debit"
    FUTURE_SCHEDULED_CREDIT = "future_scheduled_credit"
    FUTURE_SCHEDULED_DEBIT = "future_scheduled_debit"
    PENDING_DEBIT = "pending_debit"
    PENDING_CREDIT_NONSPENDABLE = "pending_credit_nonspendable"
    FAILED_IGNORED = "failed_ignored"
    CANCELLED_IGNORED = "cancelled_ignored"
    UNREALIZED_NONCASH = "unrealized_noncash"
    INTERNAL_TRANSFER_NEUTRAL = "internal_transfer_neutral"
    UNRESOLVED = "unresolved"


class LifecycleResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class EventProvenance:
    source_event_ids: tuple[str, ...]
    message_ids: tuple[str, ...] = ()
    image_ids: tuple[str, ...] = ()
    resolution_rule: str = ""
    discarded_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveEvent:
    effective_event_id: str
    source_event_ids: tuple[str, ...]
    user_id: str
    event_type: str
    category: str
    direction: Direction
    amount: object
    currency: str
    event_date: object
    settlement_date: object
    status: EventStatus
    flexibility: str | None
    minimum_allowed_amount: object
    linked_event_ids: tuple[str, ...]
    event_class: EffectiveEventClass
    resolution_status: LifecycleResolutionStatus
    resolution_reason: str
    confidence: str
    provenance: EventProvenance


@dataclass(frozen=True)
class LifecycleComponent:
    source_event_ids: tuple[str, ...]
    has_link: bool
    is_cycle: bool = False
    broken_link_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleResolutionResult:
    effective_events: tuple[EffectiveEvent, ...]
    components: tuple[LifecycleComponent, ...]
    unresolved_components: tuple[LifecycleComponent, ...]
    cyclic_components: tuple[LifecycleComponent, ...]
    broken_link_components: tuple[LifecycleComponent, ...]

    def statistics(self) -> dict[str, object]:
        linked_lengths = Counter(len(component.source_event_ids) for component in self.components if component.has_link)
        classes = Counter(event.event_class.value for event in self.effective_events)
        terminal_statuses = Counter(event.status.value for event in self.effective_events)
        return {
            "standalone_events": sum(1 for component in self.components if not component.has_link),
            "linked_event_chains": sum(1 for component in self.components if component.has_link),
            "chain_length_distribution": dict(sorted(linked_lengths.items())),
            "cancelled_terminal_chains": terminal_statuses.get(EventStatus.CANCELLED.value, 0),
            "failed_terminal_chains": terminal_statuses.get(EventStatus.FAILED.value, 0),
            "settled_terminal_chains": terminal_statuses.get(EventStatus.SETTLED.value, 0),
            "pending_terminal_chains": terminal_statuses.get(EventStatus.PENDING.value, 0),
            "unrealized_effective_records": classes.get(EffectiveEventClass.UNREALIZED_NONCASH.value, 0),
            "unresolved_chains": len(self.unresolved_components),
            "cyclic_linked_event_chains": len(self.cyclic_components),
            "broken_linked_event_chains": len(self.broken_link_components),
            "effective_event_classes": dict(sorted(classes.items())),
        }


def resolve_lifecycles(
    events: Iterable[FinancialEvent],
    *,
    request_date=None,
    messages: Iterable[MessageRecord] = (),
) -> LifecycleResolutionResult:
    event_list = tuple(events)
    message_by_event = _messages_by_event(messages)
    internal_transfer_pairs = _find_internal_transfer_pairs(event_list, message_by_event)
    internal_transfer_event_ids = {event.event_id for pair in internal_transfer_pairs for event in pair}
    components = _build_components(event_list)

    effective_events: list[EffectiveEvent] = []
    unresolved: list[LifecycleComponent] = []
    cyclic: list[LifecycleComponent] = []
    broken: list[LifecycleComponent] = []
    event_by_id = {event.event_id: event for event in event_list}

    for pair in internal_transfer_pairs:
        pair_component = LifecycleComponent(
            source_event_ids=tuple(event.event_id for event in _ordered_events(pair)),
            has_link=any(event.linked_event_id for event in pair),
        )
        effective_events.append(
            _effective_from_events(
                _ordered_events(pair),
                pair_component,
                request_date,
                EffectiveEventClass.INTERNAL_TRANSFER_NEUTRAL,
                LifecycleResolutionStatus.RESOLVED,
                "explicit_internal_transfer_evidence",
                "high",
                message_by_event,
            )
        )

    for component in components:
        if all(event_id in internal_transfer_event_ids for event_id in component.source_event_ids):
            continue
        if component.is_cycle:
            cyclic.append(component)
        if component.broken_link_event_ids:
            broken.append(component)
        component_events = tuple(event_by_id[event_id] for event_id in component.source_event_ids if event_id in event_by_id)
        effective = _resolve_component(component_events, component, request_date, message_by_event)
        effective_events.append(effective)
        if effective.resolution_status is not LifecycleResolutionStatus.RESOLVED:
            unresolved.append(component)

    return LifecycleResolutionResult(
        effective_events=tuple(effective_events),
        components=components,
        unresolved_components=tuple(unresolved),
        cyclic_components=tuple(cyclic),
        broken_link_components=tuple(broken),
    )


def _resolve_component(
    events: tuple[FinancialEvent, ...],
    component: LifecycleComponent,
    request_date,
    message_by_event: dict[str, tuple[MessageRecord, ...]],
) -> EffectiveEvent:
    ordered = _ordered_events(events)
    if component.is_cycle:
        return _effective_from_events(ordered, component, request_date, EffectiveEventClass.UNRESOLVED, LifecycleResolutionStatus.MALFORMED, "cycle_detected", "low", message_by_event)
    if component.broken_link_event_ids:
        return _effective_from_events(ordered, component, request_date, EffectiveEventClass.UNRESOLVED, LifecycleResolutionStatus.MALFORMED, "broken_linked_event_id", "low", message_by_event)
    if len(ordered) == 1:
        event_class = classify_event(ordered[0], request_date=request_date)
        return _effective_from_events(ordered, component, request_date, event_class, LifecycleResolutionStatus.RESOLVED, "standalone_status_classification", "high", message_by_event)

    cancelled = _events_with_status(ordered, EventStatus.CANCELLED)
    if cancelled:
        return _effective_from_events((cancelled[-1],), component, request_date, EffectiveEventClass.CANCELLED_IGNORED, LifecycleResolutionStatus.RESOLVED, "explicit_cancellation_precedence", "high", message_by_event, source_events=ordered)

    failed = _events_with_status(ordered, EventStatus.FAILED)
    if failed and len(failed) == len(ordered):
        return _effective_from_events((failed[-1],), component, request_date, EffectiveEventClass.FAILED_IGNORED, LifecycleResolutionStatus.RESOLVED, "failed_terminal_chain", "high", message_by_event, source_events=ordered)

    settled = _events_with_status(ordered, EventStatus.SETTLED)
    if settled:
        chosen = settled[-1]
        return _effective_from_events((chosen,), component, request_date, classify_event(chosen, request_date=request_date), LifecycleResolutionStatus.RESOLVED, "settled_fact_precedence", "high", message_by_event, source_events=ordered)

    return _effective_from_events(ordered, component, request_date, EffectiveEventClass.UNRESOLVED, LifecycleResolutionStatus.UNRESOLVED, "linked_chain_without_supported_precedence", "medium", message_by_event)


def classify_event(event: FinancialEvent, *, request_date=None) -> EffectiveEventClass:
    if event.status is EventStatus.CANCELLED:
        return EffectiveEventClass.CANCELLED_IGNORED
    if event.status is EventStatus.FAILED:
        return EffectiveEventClass.FAILED_IGNORED
    if event.status is EventStatus.UNREALIZED or event.direction is Direction.NON_CASH:
        return EffectiveEventClass.UNREALIZED_NONCASH
    if event.status is EventStatus.PENDING:
        if event.direction is Direction.CREDIT:
            return EffectiveEventClass.PENDING_CREDIT_NONSPENDABLE
        if event.direction is Direction.DEBIT:
            return EffectiveEventClass.PENDING_DEBIT
        return EffectiveEventClass.UNRESOLVED
    if event.status is EventStatus.SCHEDULED:
        if event.direction is Direction.CREDIT:
            return EffectiveEventClass.FUTURE_SCHEDULED_CREDIT
        if event.direction is Direction.DEBIT:
            return EffectiveEventClass.FUTURE_SCHEDULED_DEBIT
        return EffectiveEventClass.UNRESOLVED
    if event.status is EventStatus.SETTLED:
        if request_date is not None:
            effective_date = event.settlement_date or event.event_date
            if effective_date <= request_date:
                return EffectiveEventClass.HISTORICAL_EVIDENCE
        if event.direction is Direction.CREDIT:
            return EffectiveEventClass.FUTURE_CONFIRMED_CREDIT
        if event.direction is Direction.DEBIT:
            return EffectiveEventClass.FUTURE_CONFIRMED_DEBIT
    return EffectiveEventClass.UNRESOLVED


def _build_components(events: tuple[FinancialEvent, ...]) -> tuple[LifecycleComponent, ...]:
    event_by_id = {event.event_id: event for event in events}
    neighbors: dict[str, set[str]] = {event.event_id: set() for event in events}
    broken: dict[str, str] = {}
    for event in events:
        if event.linked_event_id:
            if event.linked_event_id in event_by_id:
                neighbors[event.event_id].add(event.linked_event_id)
                neighbors[event.linked_event_id].add(event.event_id)
            else:
                broken[event.event_id] = event.linked_event_id

    seen: set[str] = set()
    components: list[LifecycleComponent] = []
    for event in sorted(events, key=lambda item: item.source_row):
        if event.event_id in seen:
            continue
        stack = [event.event_id]
        ids: set[str] = set()
        while stack:
            current = stack.pop()
            if current in ids:
                continue
            ids.add(current)
            stack.extend(neighbors[current] - ids)
        seen.update(ids)
        ordered_ids = tuple(event_id for event_id in sorted(ids, key=lambda item: event_by_id[item].source_row))
        has_link = any(event_by_id[event_id].linked_event_id for event_id in ordered_ids)
        components.append(
            LifecycleComponent(
                source_event_ids=ordered_ids,
                has_link=has_link,
                is_cycle=_has_cycle(ordered_ids, event_by_id),
                broken_link_event_ids=tuple(event_id for event_id in ordered_ids if event_id in broken),
            )
        )
    return tuple(components)


def _has_cycle(event_ids: tuple[str, ...], event_by_id: dict[str, FinancialEvent]) -> bool:
    component_ids = set(event_ids)
    for event_id in event_ids:
        seen: set[str] = set()
        current = event_id
        while current in component_ids:
            if current in seen:
                return True
            seen.add(current)
            linked = event_by_id[current].linked_event_id
            if not linked:
                break
            current = linked
    return False


def _effective_from_events(
    chosen_events: tuple[FinancialEvent, ...],
    component: LifecycleComponent,
    request_date,
    event_class: EffectiveEventClass,
    status: LifecycleResolutionStatus,
    reason: str,
    confidence: str,
    message_by_event: dict[str, tuple[MessageRecord, ...]],
    *,
    source_events: tuple[FinancialEvent, ...] | None = None,
) -> EffectiveEvent:
    source_events = source_events or chosen_events
    chosen = _ordered_events(chosen_events)[-1]
    source_ids = tuple(event.event_id for event in _ordered_events(source_events))
    linked_ids = tuple(event.linked_event_id for event in source_events if event.linked_event_id)
    message_ids = tuple(
        message.message_id
        for event_id in source_ids
        for message in message_by_event.get(event_id, ())
    )
    discarded_ids = tuple(event_id for event_id in source_ids if event_id != chosen.event_id)
    return EffectiveEvent(
        effective_event_id="effective:" + "+".join(source_ids),
        source_event_ids=source_ids,
        user_id=chosen.user_id,
        event_type=chosen.event_type,
        category=chosen.category,
        direction=chosen.direction,
        amount=chosen.amount,
        currency=chosen.currency,
        event_date=chosen.event_date,
        settlement_date=chosen.settlement_date,
        status=chosen.status,
        flexibility=chosen.flexibility,
        minimum_allowed_amount=chosen.minimum_allowed_amount,
        linked_event_ids=linked_ids,
        event_class=event_class,
        resolution_status=status,
        resolution_reason=reason,
        confidence=confidence,
        provenance=EventProvenance(
            source_event_ids=source_ids,
            message_ids=message_ids,
            resolution_rule=reason,
            discarded_event_ids=discarded_ids,
        ),
    )


def _ordered_events(events: tuple[FinancialEvent, ...]) -> tuple[FinancialEvent, ...]:
    return tuple(sorted(events, key=lambda item: (item.event_date, item.settlement_date or item.event_date, item.source_row, item.event_id)))


def _events_with_status(events: tuple[FinancialEvent, ...], status: EventStatus) -> tuple[FinancialEvent, ...]:
    return tuple(event for event in events if event.status is status)


def _messages_by_event(messages: Iterable[MessageRecord]) -> dict[str, tuple[MessageRecord, ...]]:
    grouped: dict[str, list[MessageRecord]] = defaultdict(list)
    for message in messages:
        if message.related_event_id:
            grouped[message.related_event_id].append(message)
    return {key: tuple(value) for key, value in grouped.items()}


def _find_internal_transfer_pairs(
    events: tuple[FinancialEvent, ...],
    message_by_event: dict[str, tuple[MessageRecord, ...]],
) -> tuple[tuple[FinancialEvent, FinancialEvent], ...]:
    pairs: list[tuple[FinancialEvent, FinancialEvent]] = []
    paired_ids: set[str] = set()
    by_user_amount_currency_date: dict[tuple[object, object, object, object], list[FinancialEvent]] = defaultdict(list)
    for event in events:
        effective_date = event.settlement_date or event.event_date
        by_user_amount_currency_date[(event.user_id, event.amount, event.currency, effective_date)].append(event)

    for candidates in by_user_amount_currency_date.values():
        debits = [event for event in candidates if event.direction is Direction.DEBIT]
        credits = [event for event in candidates if event.direction is Direction.CREDIT]
        for debit in debits:
            for credit in credits:
                if debit.event_id in paired_ids or credit.event_id in paired_ids:
                    continue
                if _has_explicit_internal_transfer_evidence(debit, credit, message_by_event):
                    pairs.append((debit, credit))
                    paired_ids.add(debit.event_id)
                    paired_ids.add(credit.event_id)
    return tuple(pairs)


def _has_explicit_internal_transfer_evidence(
    first: FinancialEvent,
    second: FinancialEvent,
    message_by_event: dict[str, tuple[MessageRecord, ...]],
) -> bool:
    evidence_text = " ".join(
        [first.description, second.description]
        + [message.message_text for event in (first, second) for message in message_by_event.get(event.event_id, ())]
    ).lower()
    phrases = (
        "internal transfer",
        "between my accounts",
        "between accounts i own",
        "own accounts",
        "same user's accounts",
    )
    return any(phrase in evidence_text for phrase in phrases)
