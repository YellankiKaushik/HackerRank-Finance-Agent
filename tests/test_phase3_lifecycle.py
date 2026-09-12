import sys
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import Direction, EventStatus
from buywait.lifecycle import (
    EffectiveEventClass,
    LifecycleResolutionStatus,
    classify_event,
    resolve_lifecycles,
)
from buywait.models import FinancialEvent, MessageRecord


def event(
    event_id,
    *,
    status=EventStatus.SETTLED,
    direction=Direction.DEBIT,
    linked_event_id=None,
    event_date=date(2026, 1, 1),
    settlement_date=date(2026, 1, 1),
    amount=Decimal("100"),
    description="Test event",
    source_row=2,
):
    return FinancialEvent(
        event_id=event_id,
        user_id="user_x",
        event_type="expense",
        description=description,
        category="test",
        direction=direction,
        amount=amount,
        currency="USD",
        event_date=event_date,
        settlement_date=settlement_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility="fixed",
        minimum_allowed_amount=None,
        source_row=source_row,
    )


def message(message_id, related_event_id, text):
    return MessageRecord(
        message_id=message_id,
        user_id="user_x",
        request_id=None,
        related_event_id=related_event_id,
        sent_at=datetime(2026, 1, 1, 9, 0),
        source_type="sms",
        message_text=text,
        source_row=2,
    )


class LifecycleClassificationTests(unittest.TestCase):
    def test_status_classification(self):
        request_date = date(2026, 1, 10)
        self.assertEqual(classify_event(event("settled"), request_date=request_date), EffectiveEventClass.HISTORICAL_EVIDENCE)
        self.assertEqual(classify_event(event("cancelled", status=EventStatus.CANCELLED), request_date=request_date), EffectiveEventClass.CANCELLED_IGNORED)
        self.assertEqual(classify_event(event("failed", status=EventStatus.FAILED), request_date=request_date), EffectiveEventClass.FAILED_IGNORED)
        self.assertEqual(classify_event(event("pending_credit", status=EventStatus.PENDING, direction=Direction.CREDIT), request_date=request_date), EffectiveEventClass.PENDING_CREDIT_NONSPENDABLE)
        self.assertEqual(classify_event(event("pending_debit", status=EventStatus.PENDING), request_date=request_date), EffectiveEventClass.PENDING_DEBIT)
        self.assertEqual(classify_event(event("scheduled", status=EventStatus.SCHEDULED), request_date=request_date), EffectiveEventClass.FUTURE_SCHEDULED_DEBIT)
        self.assertEqual(classify_event(event("unrealized", status=EventStatus.UNREALIZED, direction=Direction.NON_CASH), request_date=request_date), EffectiveEventClass.UNREALIZED_NONCASH)

    def test_future_settled_event_is_not_historical_replay(self):
        future = event("future", event_date=date(2026, 2, 1), settlement_date=date(2026, 2, 1))
        self.assertEqual(classify_event(future, request_date=date(2026, 1, 10)), EffectiveEventClass.FUTURE_CONFIRMED_DEBIT)


class LinkedLifecycleTests(unittest.TestCase):
    def test_original_to_cancellation(self):
        original = event("e1", status=EventStatus.SCHEDULED, source_row=2)
        cancellation = event("e2", status=EventStatus.CANCELLED, linked_event_id="e1", source_row=3)
        result = resolve_lifecycles([original, cancellation], request_date=date(2026, 1, 1))
        self.assertEqual(result.effective_events[0].event_class, EffectiveEventClass.CANCELLED_IGNORED)
        self.assertEqual(result.effective_events[0].resolution_reason, "explicit_cancellation_precedence")
        self.assertEqual(result.effective_events[0].provenance.source_event_ids, ("e1", "e2"))

    def test_estimate_to_settlement_uses_settled_fact(self):
        estimate = event("e1", status=EventStatus.SCHEDULED, amount=Decimal("90"), source_row=2)
        settled = event("e2", status=EventStatus.SETTLED, amount=Decimal("100"), linked_event_id="e1", source_row=3)
        result = resolve_lifecycles([estimate, settled], request_date=date(2026, 1, 10))
        effective = result.effective_events[0]
        self.assertEqual(effective.amount, Decimal("100"))
        self.assertEqual(effective.event_class, EffectiveEventClass.HISTORICAL_EVIDENCE)
        self.assertEqual(effective.provenance.discarded_event_ids, ("e1",))

    def test_multistep_chain_preserves_all_sources(self):
        one = event("e1", status=EventStatus.SCHEDULED, source_row=2)
        two = event("e2", status=EventStatus.PENDING, linked_event_id="e1", source_row=3)
        three = event("e3", status=EventStatus.SETTLED, linked_event_id="e2", source_row=4)
        result = resolve_lifecycles([one, two, three], request_date=date(2026, 1, 10))
        self.assertEqual(result.effective_events[0].source_event_ids, ("e1", "e2", "e3"))
        self.assertEqual(result.effective_events[0].resolution_status, LifecycleResolutionStatus.RESOLVED)

    def test_broken_link_is_malformed(self):
        broken = event("e1", linked_event_id="missing")
        result = resolve_lifecycles([broken], request_date=date(2026, 1, 1))
        self.assertEqual(len(result.broken_link_components), 1)
        self.assertEqual(result.effective_events[0].resolution_status, LifecycleResolutionStatus.MALFORMED)

    def test_cycle_detection(self):
        first = event("e1", linked_event_id="e2", source_row=2)
        second = event("e2", linked_event_id="e1", source_row=3)
        result = resolve_lifecycles([first, second], request_date=date(2026, 1, 1))
        self.assertEqual(len(result.cyclic_components), 1)
        self.assertEqual(result.effective_events[0].event_class, EffectiveEventClass.UNRESOLVED)


class InternalTransferTests(unittest.TestCase):
    def test_explicit_internal_transfer_pair_is_neutralized(self):
        debit = event("debit", direction=Direction.DEBIT, description="Transfer out", source_row=2)
        credit = event("credit", direction=Direction.CREDIT, description="Transfer in", source_row=3)
        result = resolve_lifecycles(
            [debit, credit],
            request_date=date(2026, 1, 1),
            messages=[message("m1", "debit", "This is an internal transfer between my accounts.")],
        )
        self.assertEqual(len(result.effective_events), 1)
        self.assertEqual(result.effective_events[0].event_class, EffectiveEventClass.INTERNAL_TRANSFER_NEUTRAL)
        self.assertEqual(set(result.effective_events[0].source_event_ids), {"debit", "credit"})

    def test_similarity_alone_does_not_neutralize(self):
        debit = event("debit", direction=Direction.DEBIT, source_row=2)
        credit = event("credit", direction=Direction.CREDIT, source_row=3)
        result = resolve_lifecycles([debit, credit], request_date=date(2026, 1, 1))
        self.assertEqual(len(result.effective_events), 2)
        self.assertNotIn(EffectiveEventClass.INTERNAL_TRANSFER_NEUTRAL, {item.event_class for item in result.effective_events})


if __name__ == "__main__":
    unittest.main()
