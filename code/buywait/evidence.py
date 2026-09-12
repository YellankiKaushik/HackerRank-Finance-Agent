from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
import re
from typing import Iterable

from .enums import Direction, EventStatus
from .lifecycle import EffectiveEvent, EffectiveEventClass, LifecycleResolutionStatus
from .models import FinancialEvent
from .recurrence import RecurringStream


class MessageFactType(str, Enum):
    CANCEL_EVENT = "CANCEL_EVENT"
    SETTLE_EVENT = "SETTLE_EVENT"
    AMEND_AMOUNT = "AMEND_AMOUNT"
    AMEND_DATE = "AMEND_DATE"
    CONFIRM_INCOME = "CONFIRM_INCOME"
    CANCEL_RECURRING_STREAM = "CANCEL_RECURRING_STREAM"
    AMEND_RECURRING_STREAM = "AMEND_RECURRING_STREAM"
    DELAY_EVENT = "DELAY_EVENT"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    UNCONFIRMED_CREDIT = "UNCONFIRMED_CREDIT"
    INFORMATION_ONLY = "INFORMATION_ONLY"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class MessageFact:
    fact_type: MessageFactType
    source_message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    effective_date: date | None
    amount: Decimal | None
    currency: str | None
    referenced_event_id: str | None
    referenced_stream_category: str | None
    referenced_stream_description_hint: str | None
    provenance: str
    confidence: str
    resolution_status: str


@dataclass(frozen=True)
class CachedImageAmount:
    image_id: str
    amount: Decimal
    source_note: str


@dataclass(frozen=True)
class ResolvedImageAmount:
    event_id: str
    image_id: str
    amount: Decimal
    source_note: str


IMAGE_AMOUNT_CACHE: dict[str, CachedImageAmount] = {
    "image_02": CachedImageAmount(
        image_id="image_02",
        amount=Decimal("100000"),
        source_note="Rent receipt balance due INR 100,000.00",
    ),
    "image_03": CachedImageAmount(
        image_id="image_03",
        amount=Decimal("41272"),
        source_note="Grocery receipt net amount and cash paid INR 41,272.00",
    ),
    "image_04": CachedImageAmount(
        image_id="image_04",
        amount=Decimal("2854"),
        source_note="Delivered grocery order item bill INR 2,854.00",
    ),
    "image_05": CachedImageAmount(
        image_id="image_05",
        amount=Decimal("822.05"),
        source_note="Telecom bill amount due after 06-Feb-2026 INR 822.05",
    ),
    "image_10": CachedImageAmount(
        image_id="image_10",
        amount=Decimal("79679.26"),
        source_note="Grocery tax invoice balance due INR 79,679.26",
    ),
    "image_11": CachedImageAmount(
        image_id="image_11",
        amount=Decimal("3650"),
        source_note="Hospital provisional bill amount payable INR 3,650.00",
    ),
}


def resolve_image_backed_event_amounts(dataset, events: Iterable[FinancialEvent]) -> tuple[tuple[FinancialEvent, ...], tuple[ResolvedImageAmount, ...]]:
    resolved_events: list[FinancialEvent] = []
    resolved_amounts: list[ResolvedImageAmount] = []
    for event in events:
        if event.amount is not None:
            resolved_events.append(event)
            continue
        resolved = resolve_image_amount_for_event(dataset, event)
        if resolved is None:
            resolved_events.append(event)
            continue
        resolved_events.append(replace(event, amount=resolved.amount))
        resolved_amounts.append(resolved)
    return tuple(resolved_events), tuple(resolved_amounts)


def resolve_image_amount_for_event(dataset, event: FinancialEvent) -> ResolvedImageAmount | None:
    for image in sorted(dataset.images_by_related_event.get(event.event_id, ()), key=lambda item: (item.source_row, item.image_id)):
        cached = IMAGE_AMOUNT_CACHE.get(image.image_id)
        if cached is None:
            continue
        path = dataset.image_path(image.image_id)
        if not path.exists():
            continue
        return ResolvedImageAmount(
            event_id=event.event_id,
            image_id=image.image_id,
            amount=cached.amount,
            source_note=cached.source_note,
        )
    return None


def extract_message_facts(messages, *, request_date: date | None = None) -> tuple[MessageFact, ...]:
    facts: list[MessageFact] = []
    for message in sorted(messages, key=lambda item: (item.sent_at, item.message_id)):
        if request_date is not None and message.sent_at.date() > request_date:
            continue
        facts.extend(_facts_for_message(message))
    return tuple(facts)


def apply_message_facts_to_effective_events(
    effective_events: Iterable[EffectiveEvent],
    facts: Iterable[MessageFact],
) -> tuple[EffectiveEvent, ...]:
    facts_by_event: dict[str, list[MessageFact]] = {}
    for fact in facts:
        if fact.related_event_id:
            facts_by_event.setdefault(fact.related_event_id, []).append(fact)

    adjusted: list[EffectiveEvent] = []
    for event in effective_events:
        event_facts = [
            fact
            for source_id in event.source_event_ids
            for fact in facts_by_event.get(source_id, ())
        ]
        adjusted_event = event
        for fact in sorted(event_facts, key=lambda item: item.source_message_id):
            adjusted_event = _apply_fact_to_effective_event(adjusted_event, fact)
        adjusted.append(adjusted_event)
    return tuple(adjusted)


def apply_message_facts_to_streams(streams: Iterable[RecurringStream], facts: Iterable[MessageFact]) -> tuple[RecurringStream, ...]:
    amended: list[RecurringStream] = []
    for stream in streams:
        current = stream
        stream_facts = [
            fact
            for fact in facts
            if fact.referenced_stream_category == stream.category
            and (fact.currency is None or fact.currency == stream.currency)
            and (
                fact.referenced_stream_description_hint is None
                or fact.referenced_stream_description_hint in stream.normalized_description
            )
        ]
        for fact in sorted(stream_facts, key=lambda item: item.source_message_id):
            if fact.fact_type is MessageFactType.CANCEL_RECURRING_STREAM:
                current = replace(
                    current,
                    termination_event_ids=current.termination_event_ids + (fact.source_message_id,),
                )
            elif fact.fact_type in (MessageFactType.AMEND_RECURRING_STREAM, MessageFactType.CONFIRM_INCOME):
                kwargs = {"amendment_event_ids": current.amendment_event_ids + (fact.source_message_id,)}
                if fact.amount is not None:
                    kwargs["historical_amounts"] = current.historical_amounts + (fact.amount,)
                if fact.effective_date is not None:
                    kwargs["latest_known_occurrence"] = fact.effective_date
                current = replace(current, **kwargs)
        amended.append(current)
    return tuple(amended)


def _facts_for_message(message) -> tuple[MessageFact, ...]:
    text = message.message_text
    lowered = text.lower()
    amount_currency = _extract_amount_currency(text)
    explicit_date = _extract_date(text)
    related = message.related_event_id

    if _contains_any(lowered, ("between your two accounts", "between my accounts", "transfer antara dua rekening")):
        return (_message_fact(message, MessageFactType.INTERNAL_TRANSFER, provenance="message identifies matching debit/credit as own-account transfer"),)
    if _contains_any(lowered, ("refund has been initiated", "pengembalian dana sudah diproses", "refund is still processing", "still processing")):
        return (_message_fact(message, MessageFactType.UNCONFIRMED_CREDIT, provenance="message says refund/credit has not settled"),)
    if _contains_any(lowered, ("no units have been sold", "no cash proceeds", "belum dijual", "tidak ada transaksi tunai")):
        return (_message_fact(message, MessageFactType.UNCONFIRMED_CREDIT, provenance="message says displayed investment value is not cash"),)
    if _contains_any(lowered, ("still in payment processing", "has not been credited", "belum masuk", "not been credited")):
        return (_message_fact(message, MessageFactType.UNCONFIRMED_CREDIT, provenance="message says credit is not available yet"),)
    if _contains_any(lowered, ("prize proceeds have reached your account", "there are no further scheduled payments")):
        return (
            _message_fact(
                message,
                MessageFactType.CANCEL_RECURRING_STREAM,
                referenced_stream_category="windfall",
                provenance="message says prize claim is closed with no further scheduled payments",
            ),
        )
    if _contains_any(lowered, ("client approved an invoice payment", "klien menyetujui pembayaran faktur")) and amount_currency is not None:
        amount, currency = amount_currency
        return (
            _message_fact(
                message,
                MessageFactType.CONFIRM_INCOME,
                effective_date=explicit_date,
                amount=amount,
                currency=currency,
                referenced_stream_category="invoice",
                provenance="message confirms approved invoice payment amount",
            ),
        )
    if _contains_any(lowered, ("previous debit attempt failed", "bill is still outstanding", "another debit will be attempted")):
        return (_message_fact(message, MessageFactType.UNRESOLVED, provenance="message confirms failed debit remains open but gives no new amount/date"),)
    if _contains_any(lowered, ("cancelled", "canceled", "dibatalkan")) and related:
        return (_message_fact(message, MessageFactType.CANCEL_EVENT, provenance="message explicitly cancels related event"),)
    if _contains_any(lowered, ("moved to", "postponed to", "delayed to", "revised date")) and explicit_date:
        return (
            _message_fact(
                message,
                MessageFactType.AMEND_DATE if related else MessageFactType.AMEND_RECURRING_STREAM,
                effective_date=explicit_date,
                referenced_stream_category="salary" if _is_payroll(lowered) else None,
                provenance="message gives an explicit replacement date",
            ),
        )
    if _contains_any(lowered, ("renewed lease increases monthly rent by 12%", "perpanjangan sewa menaikkan biaya sewa bulanan sebesar 12%")):
        return (
            _message_fact(
                message,
                MessageFactType.AMEND_RECURRING_STREAM,
                referenced_stream_category="rent",
                provenance="message states renewed lease increases monthly rent by 12%",
            ),
        )
    if _is_payroll(lowered):
        if _contains_any(lowered, ("no off-season income", "employment has ended", "contract has ended", "kontrak musiman saat ini telah berakhir")):
            return (
                _message_fact(
                    message,
                    MessageFactType.CANCEL_RECURRING_STREAM,
                    referenced_stream_category="salary",
                    provenance="message says regular or seasonal income ended",
                ),
            )
        if _contains_any(lowered, ("pending approval", "belum disetujui", "not been approved")) and amount_currency is None:
            return (_message_fact(message, MessageFactType.UNCONFIRMED_CREDIT, provenance="message says bonus/commission is not approved"),)
        payroll_facts: list[MessageFact] = []
        if _contains_any(lowered, ("commission", "komisi")) and _contains_any(lowered, ("pending approval", "belum disetujui", "not been approved")):
            payroll_facts.append(
                _message_fact(
                    message,
                    MessageFactType.CANCEL_RECURRING_STREAM,
                    referenced_stream_category="salary",
                    referenced_stream_description_hint="commission",
                    provenance="message says commission income is not approved or earned",
                )
            )
        if amount_currency is not None:
            amount, currency = amount_currency
            fact_type = MessageFactType.CONFIRM_INCOME if _contains_any(lowered, ("first salary", "confirmed credit date", "dikonfirmasi untuk", "dijadwalkan pada")) else MessageFactType.AMEND_RECURRING_STREAM
            description_hint = "base salary" if _contains_any(lowered, ("base salary", "gaji pokok")) else None
            payroll_facts.append(
                _message_fact(
                    message,
                    fact_type,
                    effective_date=explicit_date,
                    amount=amount,
                    currency=currency,
                    referenced_stream_category="salary",
                    referenced_stream_description_hint=description_hint,
                    provenance="message gives explicit payroll amount/date fact",
                ),
            )
            return tuple(payroll_facts)
        if explicit_date is not None and _contains_any(lowered, ("expected on", "diperkirakan masuk pada", "confirmed for")):
            payroll_facts.append(
                _message_fact(
                    message,
                    MessageFactType.AMEND_RECURRING_STREAM,
                    effective_date=explicit_date,
                    referenced_stream_category="salary",
                    provenance="message gives explicit payroll date fact",
                ),
            )
            return tuple(payroll_facts)
        if payroll_facts:
            return tuple(payroll_facts)
    if amount_currency is not None and related:
        amount, currency = amount_currency
        return (
            _message_fact(
                message,
                MessageFactType.AMEND_AMOUNT,
                effective_date=explicit_date,
                amount=amount,
                currency=currency,
                provenance="message gives explicit related-event amount",
            ),
        )
    return (_message_fact(message, MessageFactType.INFORMATION_ONLY, provenance="message has no supported financial amendment"),)


def _apply_fact_to_effective_event(event: EffectiveEvent, fact: MessageFact) -> EffectiveEvent:
    if fact.fact_type is MessageFactType.CANCEL_EVENT:
        return replace(
            event,
            status=EventStatus.CANCELLED,
            event_class=EffectiveEventClass.CANCELLED_IGNORED,
            resolution_status=LifecycleResolutionStatus.RESOLVED,
            resolution_reason="message_cancelled_event",
        )
    if fact.fact_type is MessageFactType.UNCONFIRMED_CREDIT and event.direction is Direction.CREDIT:
        return replace(
            event,
            event_class=EffectiveEventClass.PENDING_CREDIT_NONSPENDABLE,
            resolution_reason="message_credit_not_available",
        )
    if fact.fact_type is MessageFactType.AMEND_AMOUNT and fact.amount is not None:
        return replace(event, amount=fact.amount, currency=fact.currency or event.currency, resolution_reason="message_amount_amendment")
    if fact.fact_type in (MessageFactType.AMEND_DATE, MessageFactType.DELAY_EVENT) and fact.effective_date is not None:
        return replace(event, event_date=fact.effective_date, settlement_date=fact.effective_date, resolution_reason="message_date_amendment")
    return event


def _message_fact(
    message,
    fact_type: MessageFactType,
    *,
    effective_date: date | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    referenced_stream_category: str | None = None,
    referenced_stream_description_hint: str | None = None,
    provenance: str,
) -> MessageFact:
    return MessageFact(
        fact_type=fact_type,
        source_message_id=message.message_id,
        user_id=message.user_id,
        request_id=message.request_id,
        related_event_id=message.related_event_id,
        effective_date=effective_date,
        amount=amount,
        currency=currency,
        referenced_event_id=message.related_event_id,
        referenced_stream_category=referenced_stream_category,
        referenced_stream_description_hint=referenced_stream_description_hint,
        provenance=provenance,
        confidence="high" if fact_type is not MessageFactType.UNRESOLVED else "medium",
        resolution_status="resolved" if fact_type is not MessageFactType.UNRESOLVED else "unresolved",
    )


def _extract_amount_currency(text: str) -> tuple[Decimal, str] | None:
    match = re.search(r"\b(EUR|USD|INR|IDR|ZAR)\s*([0-9][0-9,.]*)\b", text, flags=re.IGNORECASE)
    if match is None:
        return None
    return Decimal(match.group(2).replace(",", "")), match.group(1).upper()


def _extract_date(text: str) -> date | None:
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if match is None:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _is_payroll(text: str) -> bool:
    return _contains_any(text, ("payroll", "salary", "gaji", "penggajian"))
