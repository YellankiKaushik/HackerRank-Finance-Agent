from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .loaders import LoadedDataset
from .models import (
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    ImageRecord,
    MessageRecord,
    PaymentOption,
    Request,
)


class MessageTemporalRelation(str, Enum):
    BEFORE_REQUEST_DATE = "before_request_date"
    ON_REQUEST_DATE_TIME_UNRESOLVED = "on_request_date_time_unresolved"
    AFTER_REQUEST_DATE = "after_request_date"


class MessageTemporalPolicy(str, Enum):
    RETAIN_ALL_WITH_DATE_CLASSIFICATION = "retain_all_with_date_classification"


@dataclass(frozen=True)
class MessageEvidence:
    message: MessageRecord
    temporal_relation: MessageTemporalRelation
    reason: str


@dataclass(frozen=True)
class RequiredFxReference:
    source_event_id: str
    rate_date: object
    from_currency: str
    to_currency: str
    exchange_rate: ExchangeRate | None
    reason: str


@dataclass(frozen=True)
class RequestSnapshot:
    request: Request
    profile: FinancialProfile
    user_events: tuple[FinancialEvent, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[MessageEvidence, ...]
    images: tuple[ImageRecord, ...]
    required_fx_references: tuple[RequiredFxReference, ...]
    message_temporal_policy: MessageTemporalPolicy


def build_request_snapshot(
    dataset: LoadedDataset,
    request_id: str,
    *,
    message_temporal_policy: MessageTemporalPolicy = MessageTemporalPolicy.RETAIN_ALL_WITH_DATE_CLASSIFICATION,
) -> RequestSnapshot:
    request = dataset.request_by_id[request_id]
    profile = dataset.profile_by_user[request.user_id]
    user_events = dataset.events_by_user.get(request.user_id, ())
    event_ids = {event.event_id for event in user_events}

    messages = _collect_messages(dataset, request.user_id, request.request_id, event_ids, request.request_date)
    images = _collect_images(dataset, request.user_id, request.request_id, event_ids)
    fx_refs = _collect_required_fx_references(dataset, user_events, profile.home_currency)

    return RequestSnapshot(
        request=request,
        profile=profile,
        user_events=user_events,
        payment_options=dataset.payment_options_by_request.get(request.request_id, ()),
        messages=messages,
        images=images,
        required_fx_references=fx_refs,
        message_temporal_policy=message_temporal_policy,
    )


def validate_snapshots_for_all_requests(dataset: LoadedDataset) -> tuple[RequestSnapshot, ...]:
    return tuple(build_request_snapshot(dataset, request.request_id) for request in dataset.requests)


def _collect_messages(
    dataset: LoadedDataset,
    user_id: str,
    request_id: str,
    event_ids: set[str],
    request_date,
) -> tuple[MessageEvidence, ...]:
    by_id: dict[str, MessageRecord] = {}
    for message in dataset.messages_by_user.get(user_id, ()):
        by_id[message.message_id] = message
    for message in dataset.messages_by_request.get(request_id, ()):
        by_id[message.message_id] = message
    for event_id in event_ids:
        for message in dataset.messages_by_related_event.get(event_id, ()):
            if message.user_id == user_id:
                by_id[message.message_id] = message

    return tuple(
        MessageEvidence(message=message, temporal_relation=_message_temporal_relation(message, request_date), reason=_message_reason(message, request_id, event_ids))
        for message in sorted(by_id.values(), key=lambda item: (item.sent_at, item.source_row, item.message_id))
    )


def _collect_images(dataset: LoadedDataset, user_id: str, request_id: str, event_ids: set[str]) -> tuple[ImageRecord, ...]:
    by_id: dict[str, ImageRecord] = {}
    for image in dataset.images_by_user.get(user_id, ()):
        by_id[image.image_id] = image
    for image in dataset.images_by_request.get(request_id, ()):
        by_id[image.image_id] = image
    for event_id in event_ids:
        for image in dataset.images_by_related_event.get(event_id, ()):
            if image.user_id == user_id:
                by_id[image.image_id] = image
    return tuple(sorted(by_id.values(), key=lambda item: (item.source_row, item.image_id)))


def _collect_required_fx_references(
    dataset: LoadedDataset,
    events: tuple[FinancialEvent, ...],
    home_currency: str,
) -> tuple[RequiredFxReference, ...]:
    refs: list[RequiredFxReference] = []
    for event in events:
        if event.currency == home_currency or event.settlement_date is None:
            continue
        rate = dataset.exchange_rates_by_date_and_pair.get((event.settlement_date, event.currency, home_currency))
        refs.append(
            RequiredFxReference(
                source_event_id=event.event_id,
                rate_date=event.settlement_date,
                from_currency=event.currency,
                to_currency=home_currency,
                exchange_rate=rate,
                reason="foreign_currency_event_with_settlement_date",
            )
        )
    return tuple(refs)


def _message_temporal_relation(message: MessageRecord, request_date) -> MessageTemporalRelation:
    sent_date = message.sent_at.date()
    if sent_date < request_date:
        return MessageTemporalRelation.BEFORE_REQUEST_DATE
    if sent_date == request_date:
        return MessageTemporalRelation.ON_REQUEST_DATE_TIME_UNRESOLVED
    return MessageTemporalRelation.AFTER_REQUEST_DATE


def _message_reason(message: MessageRecord, request_id: str, event_ids: set[str]) -> str:
    parts: list[str] = []
    if message.request_id == request_id:
        parts.append("request_id_match")
    if message.related_event_id in event_ids:
        parts.append("related_event_id_match")
    if message.request_id is None and message.related_event_id is None:
        parts.append("user_level_blank_links_retained")
    if not parts:
        parts.append("user_id_match")
    return "|".join(parts)
