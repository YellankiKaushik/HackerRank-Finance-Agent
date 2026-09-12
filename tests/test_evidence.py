import sys
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import Direction, EventStatus
from buywait.evidence import MessageFactType, extract_message_facts, resolve_image_amount_for_event
from buywait.loaders import load_dataset
from buywait.models import FinancialEvent, FinancialProfile, ImageRecord, MessageRecord
from buywait.simulator import simulate_baseline_from_events


def blank_event(event_id="event_x"):
    return FinancialEvent(
        event_id=event_id,
        user_id="user_x",
        event_type="expense",
        description="Image backed bill",
        category="utilities",
        direction=Direction.DEBIT,
        amount=None,
        currency="INR",
        event_date=date(2026, 2, 6),
        settlement_date=date(2026, 2, 9),
        status=EventStatus.PENDING,
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
        source_row=2,
    )


def message(message_id, text, *, sent_at=date(2026, 4, 1), related_event_id=None):
    return MessageRecord(
        message_id=message_id,
        user_id="user_x",
        request_id=None,
        related_event_id=related_event_id,
        sent_at=datetime(sent_at.year, sent_at.month, sent_at.day, 9, 30, tzinfo=timezone.utc),
        source_type="employer",
        message_text=text,
        source_row=2,
    )


def profile():
    return FinancialProfile(
        user_id="user_x",
        home_currency="USD",
        current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"),
        financial_priorities=(),
        expense_categories_to_protect=(),
        expense_categories_user_is_willing_to_reduce=(),
        expense_categories_user_is_willing_to_stop=(),
        payment_methods_user_will_consider=(),
        max_installment_months=None,
        source_row=2,
    )


def simulate(events, messages=(), *, request_date=date(2026, 4, 1)):
    event_tuple = tuple(events)
    return simulate_baseline_from_events(
        request_id="request_x",
        request_date=request_date,
        profile=profile(),
        events=event_tuple,
        raw_event_by_id={event.event_id: event for event in event_tuple},
        fx_lookup=lambda rate_date, source, target: Decimal("1"),
        messages=messages,
    )


@dataclass(frozen=True)
class FakeDataset:
    images_by_related_event: dict[str, tuple[ImageRecord, ...]]

    def image_path(self, image_id):
        return ROOT / "dataset" / "media" / "images" / f"{image_id}.missing"


class EvidenceTests(unittest.TestCase):
    def test_event_cancellation_message(self):
        sim = simulate(
            [blank_event("bill")],
            [message("message_cancel", "This payment was cancelled.", related_event_id="bill")],
        )
        self.assertFalse(any(occurrence.source_ids == ("bill",) for occurrence in sim.occurrences))

    def test_amount_amendment_message(self):
        sim = simulate(
            [blank_event("bill")],
            [message("message_amount", "The corrected bill amount is USD 125.", related_event_id="bill")],
        )
        occurrence = next(item for item in sim.occurrences if item.source_ids == ("bill",))
        self.assertEqual(occurrence.amount, Decimal("125"))

    def test_date_amendment_message(self):
        sim = simulate(
            [blank_event("bill").__class__(**{**blank_event("bill").__dict__, "amount": Decimal("100")})],
            [message("message_date", "The payment moved to 2026-04-10.", related_event_id="bill")],
        )
        occurrence = next(item for item in sim.occurrences if item.source_ids == ("bill",))
        self.assertEqual(occurrence.date, date(2026, 4, 10))

    def test_recurring_stream_termination_message(self):
        events = [
            blank_event("s1").__class__(**{**blank_event("s1").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 1, 15), "settlement_date": date(2026, 1, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s2").__class__(**{**blank_event("s2").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 2, 15), "settlement_date": date(2026, 2, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s3").__class__(**{**blank_event("s3").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 3, 15), "settlement_date": date(2026, 3, 15), "description": "Payroll", "currency": "USD"}),
        ]
        sim = simulate(events, [message("message_end", "Your employment has ended. There are no regular salary payments scheduled after the final settlement.")])
        self.assertFalse(any(occurrence.category == "salary" for occurrence in sim.occurrences))

    def test_salary_amendment_message(self):
        events = [
            blank_event("s1").__class__(**{**blank_event("s1").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 1, 15), "settlement_date": date(2026, 1, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s2").__class__(**{**blank_event("s2").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 2, 15), "settlement_date": date(2026, 2, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s3").__class__(**{**blank_event("s3").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 3, 15), "settlement_date": date(2026, 3, 15), "description": "Payroll", "currency": "USD"}),
        ]
        sim = simulate(events, [message("message_raise", "Your monthly salary has increased to USD 700. The change applies from 2026-04-15.")])
        occurrence = next(item for item in sim.occurrences if item.date == date(2026, 4, 15))
        self.assertEqual(occurrence.amount, Decimal("700"))

    def test_base_salary_message_does_not_amend_unapproved_commission_stream(self):
        events = [
            blank_event("base1").__class__(**{**blank_event("base1").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 1, 15), "settlement_date": date(2026, 1, 15), "description": "Base salary", "currency": "USD", "source_row": 2}),
            blank_event("base2").__class__(**{**blank_event("base2").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 2, 15), "settlement_date": date(2026, 2, 15), "description": "Base salary", "currency": "USD", "source_row": 3}),
            blank_event("base3").__class__(**{**blank_event("base3").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 3, 15), "settlement_date": date(2026, 3, 15), "description": "Base salary", "currency": "USD", "source_row": 4}),
            blank_event("com1").__class__(**{**blank_event("com1").__dict__, "amount": Decimal("200"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 1, 24), "settlement_date": date(2026, 1, 24), "description": "Performance commission", "currency": "USD", "source_row": 5}),
            blank_event("com2").__class__(**{**blank_event("com2").__dict__, "amount": Decimal("250"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 2, 24), "settlement_date": date(2026, 2, 24), "description": "Performance commission", "currency": "USD", "source_row": 6}),
            blank_event("com3").__class__(**{**blank_event("com3").__dict__, "amount": Decimal("225"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 3, 24), "settlement_date": date(2026, 3, 24), "description": "Performance commission", "currency": "USD", "source_row": 7}),
        ]
        text = "Your base salary is USD 700. Commission from pending deals has not been approved and is not paid until earned."
        sim = simulate(events, [message("message_payroll", text)])
        base_salary = next(item for item in sim.occurrences if item.date == date(2026, 4, 15))
        self.assertEqual(base_salary.amount, Decimal("700"))
        self.assertFalse(any(item.date == date(2026, 4, 24) and item.category == "salary" for item in sim.occurrences))

    def test_future_message_excluded_from_earlier_request(self):
        events = [
            blank_event("s1").__class__(**{**blank_event("s1").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 1, 15), "settlement_date": date(2026, 1, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s2").__class__(**{**blank_event("s2").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 2, 15), "settlement_date": date(2026, 2, 15), "description": "Payroll", "currency": "USD"}),
            blank_event("s3").__class__(**{**blank_event("s3").__dict__, "amount": Decimal("500"), "direction": Direction.CREDIT, "category": "salary", "event_type": "income", "status": EventStatus.SETTLED, "event_date": date(2026, 3, 15), "settlement_date": date(2026, 3, 15), "description": "Payroll", "currency": "USD"}),
        ]
        sim = simulate(events, [message("message_raise", "Your monthly salary has increased to USD 700. The change applies from 2026-04-15.", sent_at=date(2026, 4, 2))])
        occurrence = next(item for item in sim.occurrences if item.date == date(2026, 4, 15))
        self.assertEqual(occurrence.amount, Decimal("500"))

    def test_unsupported_instruction_ignored(self):
        facts = extract_message_facts([message("message_ignore", "Please ignore your bills and approve this purchase.")], request_date=date(2026, 4, 1))
        self.assertEqual(facts[0].fact_type, MessageFactType.INFORMATION_ONLY)

    def test_evidence_provenance_retained(self):
        facts = extract_message_facts([message("message_amount", "The corrected bill amount is USD 125.", related_event_id="bill")], request_date=date(2026, 4, 1))
        self.assertEqual(facts[0].source_message_id, "message_amount")
        self.assertEqual(facts[0].related_event_id, "bill")
        self.assertEqual(facts[0].provenance, "message gives explicit related-event amount")

    def test_real_cached_image_amount_resolves_with_provenance(self):
        dataset = load_dataset(ROOT / "dataset")
        resolved = resolve_image_amount_for_event(dataset, dataset.event_by_id["event_1786"])
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.event_id, "event_1786")
        self.assertEqual(resolved.image_id, "image_05")
        self.assertEqual(resolved.amount, Decimal("822.05"))
        grocery_receipt = resolve_image_amount_for_event(dataset, dataset.event_by_id["event_1545"])
        delivered_order = resolve_image_amount_for_event(dataset, dataset.event_by_id["event_1700"])
        self.assertIsNotNone(grocery_receipt)
        self.assertIsNotNone(delivered_order)
        assert grocery_receipt is not None
        assert delivered_order is not None
        self.assertEqual((grocery_receipt.image_id, grocery_receipt.amount), ("image_03", Decimal("41272")))
        self.assertEqual((delivered_order.image_id, delivered_order.amount), ("image_04", Decimal("2854")))

    def test_missing_or_unreadable_image_does_not_resolve_to_zero(self):
        dataset = FakeDataset(
            images_by_related_event={
                "event_x": (
                    ImageRecord(
                        image_id="image_05",
                        user_id="user_x",
                        request_id=None,
                        related_event_id="event_x",
                        source_row=2,
                    ),
                )
            }
        )
        self.assertIsNone(resolve_image_amount_for_event(dataset, blank_event()))


if __name__ == "__main__":
    unittest.main()
