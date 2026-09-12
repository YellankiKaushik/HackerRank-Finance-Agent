import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import Direction, EventStatus
from buywait.lifecycle import resolve_lifecycles
from buywait.loaders import load_dataset
from buywait.models import FinancialEvent
from buywait.recurrence import (
    AmountBehavior,
    CadenceType,
    event_matches_stream_identity,
    generate_occurrences,
    infer_recurring_streams,
    infer_streams_for_snapshot,
    next_occurrence_after,
)
from buywait.snapshot import build_request_snapshot


def event(
    event_id,
    *,
    user_id="user_x",
    direction=Direction.DEBIT,
    amount=Decimal("100"),
    event_date=date(2026, 1, 1),
    settlement_date=None,
    status=EventStatus.SETTLED,
    category="rent",
    event_type="expense",
    description="Apartment rent",
    source_row=2,
):
    return FinancialEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency="USD",
        event_date=event_date,
        settlement_date=settlement_date or event_date,
        status=status,
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
        source_row=source_row,
    )


def infer(events, request_date=date(2026, 5, 1)):
    result = resolve_lifecycles(events, request_date=request_date)
    return infer_recurring_streams(result.effective_events, {item.event_id: item for item in events})


class RecurrenceTests(unittest.TestCase):
    def test_obvious_monthly_salary(self):
        events = [
            event("e1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll credit", amount=Decimal("5000"), event_date=date(2026, 1, 15), source_row=2),
            event("e2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll credit", amount=Decimal("5000"), event_date=date(2026, 2, 15), source_row=3),
            event("e3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll credit", amount=Decimal("5000"), event_date=date(2026, 3, 15), source_row=4),
        ]
        streams = infer(events)
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].cadence, CadenceType.MONTHLY)
        self.assertEqual(streams[0].amount_behavior, AmountBehavior.FIXED)

    def test_obvious_monthly_rent_and_february_generation(self):
        events = [
            event("e1", event_date=date(2025, 12, 31), source_row=2),
            event("e2", event_date=date(2026, 1, 31), source_row=3),
            event("e3", event_date=date(2026, 2, 28), source_row=4),
        ]
        stream = infer(events, request_date=date(2026, 3, 1))[0]
        self.assertTrue(stream.month_end)
        self.assertEqual(next_occurrence_after(stream, date(2026, 3, 1)), date(2026, 3, 31))
        self.assertEqual(generate_occurrences(stream, date(2026, 3, 1), date(2026, 5, 31)), (date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)))

    def test_one_time_event_rejected(self):
        self.assertEqual(infer([event("e1")]), ())

    def test_unrelated_same_amount_events_not_merged(self):
        events = [
            event("e1", category="rent", description="Apartment rent", event_date=date(2026, 1, 1), source_row=2),
            event("e2", category="utilities", description="Water bill", event_date=date(2026, 2, 1), source_row=3),
            event("e3", category="insurance", description="Insurance", event_date=date(2026, 3, 1), source_row=4),
        ]
        self.assertEqual(infer(events), ())

    def test_cancelled_failed_unrealized_and_unresolved_excluded(self):
        events = [
            event("e1", event_date=date(2026, 1, 1), source_row=2),
            event("e2", event_date=date(2026, 2, 1), status=EventStatus.CANCELLED, source_row=3),
            event("e3", event_date=date(2026, 3, 1), status=EventStatus.FAILED, source_row=4),
            event("e4", event_date=date(2026, 4, 1), status=EventStatus.UNREALIZED, direction=Direction.NON_CASH, source_row=5),
        ]
        self.assertEqual(infer(events), ())

    def test_future_information_not_used(self):
        events = [
            event("e1", event_date=date(2026, 1, 1), source_row=2),
            event("e2", event_date=date(2026, 2, 1), source_row=3),
            event("e3", event_date=date(2026, 3, 1), source_row=4),
            event("future", event_date=date(2026, 4, 1), source_row=5),
        ]
        streams = infer(events, request_date=date(2026, 2, 15))
        self.assertEqual(streams, ())

    def test_variable_amount_classification(self):
        events = [
            event("e1", amount=Decimal("100"), event_date=date(2026, 1, 6), description="Utility bill", category="utilities", source_row=2),
            event("e2", amount=Decimal("125"), event_date=date(2026, 2, 6), description="Utility bill", category="utilities", source_row=3),
            event("e3", amount=Decimal("110"), event_date=date(2026, 3, 6), description="Utility bill", category="utilities", source_row=4),
        ]
        stream = infer(events)[0]
        self.assertEqual(stream.amount_behavior, AmountBehavior.VARIABLE)
        self.assertEqual(stream.historical_amounts, (Decimal("100"), Decimal("125"), Decimal("110")))

    def test_fixed_day_cadence(self):
        events = [
            event("e1", event_date=date(2026, 1, 1), description="Grocery delivery", category="groceries", source_row=2),
            event("e2", event_date=date(2026, 1, 15), description="Grocery delivery", category="groceries", source_row=3),
            event("e3", event_date=date(2026, 1, 29), description="Grocery delivery", category="groceries", source_row=4),
        ]
        stream = infer(events)[0]
        self.assertEqual(stream.cadence, CadenceType.FIXED_DAYS)
        self.assertEqual(stream.cadence_interval_days, 14)

    def test_explicit_future_event_stream_identity_support(self):
        history = [
            event("e1", event_date=date(2026, 1, 4), source_row=2),
            event("e2", event_date=date(2026, 2, 4), source_row=3),
            event("e3", event_date=date(2026, 3, 4), source_row=4),
            event("future", event_date=date(2026, 4, 4), status=EventStatus.SCHEDULED, source_row=5),
        ]
        result = resolve_lifecycles(history, request_date=date(2026, 3, 10))
        streams = infer_recurring_streams(result.effective_events, {item.event_id: item for item in history})
        future_effective = next(item for item in result.effective_events if item.source_event_ids[-1] == "future")
        self.assertTrue(event_matches_stream_identity(streams[0], future_effective, {item.event_id: item for item in history}))

    def test_row_order_does_not_change_inference(self):
        events = [
            event("e3", event_date=date(2026, 3, 1), source_row=4),
            event("e1", event_date=date(2026, 1, 1), source_row=2),
            event("e2", event_date=date(2026, 2, 1), source_row=3),
        ]
        forward = infer(events)
        reverse = infer(list(reversed(events)))
        self.assertEqual(forward, reverse)


class RealDataRecurrenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_real_evaluation_snapshots_infer_without_forecasting(self):
        counts = []
        for request in self.dataset.requests:
            snapshot = build_request_snapshot(self.dataset, request.request_id)
            streams = infer_streams_for_snapshot(snapshot, self.dataset.event_by_id)
            counts.append(len(streams))
        self.assertEqual(len(counts), 250)
        self.assertGreater(min(counts), 0)

    def test_public_sample_users_have_structural_recurrence(self):
        counts = []
        for sample in self.dataset.sample_requests:
            events = self.dataset.events_by_user[sample.request.user_id]
            result = resolve_lifecycles(events, request_date=sample.request.request_date)
            streams = infer_recurring_streams(result.effective_events, self.dataset.event_by_id)
            counts.append(len(streams))
        self.assertEqual(len(counts), 25)
        self.assertGreater(min(counts), 0)


if __name__ == "__main__":
    unittest.main()
