import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.capacity import calculate_capacity_from_simulation, one_time_payment_is_safe, plan_safety, RequestPayment
from buywait.enums import Direction, EventStatus, PaymentMethod, RequestType
from buywait.models import FinancialEvent, FinancialProfile, Request
from buywait.simulator import simulate_baseline_from_events


def profile(balance=Decimal("1000"), minimum=Decimal("100")):
    return FinancialProfile(
        user_id="user_x",
        home_currency="USD",
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=(),
        expense_categories_to_protect=("rent",),
        expense_categories_user_is_willing_to_reduce=("entertainment",),
        expense_categories_user_is_willing_to_stop=("music",),
        payment_methods_user_will_consider=(PaymentMethod.INSTALLMENTS,),
        max_installment_months=3,
        source_row=2,
    )


def request(amount=Decimal("500"), request_date=date(2026, 4, 1)):
    return Request(
        request_id="request_x",
        user_id="user_x",
        request_date=request_date,
        request_type=RequestType.PURCHASE,
        requested_amount=amount,
        desired_completion_date=date(2026, 5, 1),
        allows_partial_payment=True,
        request_text="Can I afford it?",
        source_row=2,
    )


def event(
    event_id,
    *,
    direction=Direction.DEBIT,
    amount=Decimal("100"),
    event_date=date(2026, 4, 1),
    status=EventStatus.SCHEDULED,
    category="rent",
    event_type="expense",
    description="Apartment rent",
    source_row=2,
):
    return FinancialEvent(
        event_id=event_id,
        user_id="user_x",
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency="USD",
        event_date=event_date,
        settlement_date=event_date,
        status=status,
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
        source_row=source_row,
    )


def simulate(events, *, prof=None, req=None):
    req = req or request()
    event_tuple = tuple(events)
    return simulate_baseline_from_events(
        request_id=req.request_id,
        request_date=req.request_date,
        profile=prof or profile(),
        events=event_tuple,
        raw_event_by_id={item.event_id: item for item in event_tuple},
        fx_lookup=lambda rate_date, source, target: Decimal("1"),
    )


class CapacityTests(unittest.TestCase):
    def test_full_requested_amount_safe_today(self):
        req = request(amount=Decimal("500"))
        sim = simulate([], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("500"))
        self.assertEqual(result.earliest_date_for_full_payment, date(2026, 4, 1))

    def test_partially_safe_today(self):
        req = request(amount=Decimal("1000"))
        sim = simulate([event("future", amount=Decimal("450"), event_date=date(2026, 4, 5))], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("450"))

    def test_zero_safe_amount(self):
        req = request(amount=Decimal("100"))
        sim = simulate([event("future", amount=Decimal("900"), event_date=date(2026, 4, 5))], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("0"))
        self.assertIsNone(result.earliest_date_for_full_payment)

    def test_baseline_deficit_preserved(self):
        req = request(amount=Decimal("1"))
        sim = simulate([event("future", amount=Decimal("950"), event_date=date(2026, 4, 5))], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertFalse(sim.baseline_safe)
        self.assertEqual(result.amount_safe_to_pay, Decimal("0"))

    def test_amount_capped_at_requested_amount(self):
        req = request(amount=Decimal("50"))
        sim = simulate([], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("50"))

    def test_preferences_do_not_alter_capacity(self):
        req = request(amount=Decimal("500"))
        sim = simulate([], req=req, prof=profile())
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.earliest_date_for_full_payment, date(2026, 4, 1))
        self.assertEqual(result.amount_safe_to_pay, Decimal("500"))

    def test_optional_changes_do_not_alter_baseline_capacity(self):
        req = request(amount=Decimal("1000"))
        sim = simulate(
            [
                event("rent", amount=Decimal("450"), event_date=date(2026, 4, 5)),
                event("flex", amount=Decimal("100"), event_date=date(2026, 4, 7), category="entertainment"),
            ],
            req=req,
        )
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("350"))

    def test_earliest_full_date_later(self):
        req = request(amount=Decimal("1200"))
        sim = simulate(
            [
                event(
                    "salary",
                    direction=Direction.CREDIT,
                    amount=Decimal("500"),
                    event_date=date(2026, 4, 10),
                    category="salary",
                    event_type="income",
                    description="Payroll",
                )
            ],
            req=req,
        )
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("900"))
        self.assertEqual(result.earliest_date_for_full_payment, date(2026, 4, 10))

    def test_never_safe_full_payment(self):
        req = request(amount=Decimal("1000"))
        sim = simulate([], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        self.assertEqual(result.amount_safe_to_pay, Decimal("900"))
        self.assertIsNone(result.earliest_date_for_full_payment)

    def test_prefix_baseline_deficit_prevents_later_capacity(self):
        req = request(amount=Decimal("300"))
        sim = simulate(
            [
                event("early_bill", amount=Decimal("950"), event_date=date(2026, 4, 2)),
                event(
                    "later_salary",
                    direction=Direction.CREDIT,
                    amount=Decimal("1000"),
                    event_date=date(2026, 4, 10),
                    category="salary",
                    event_type="income",
                    description="Payroll",
                ),
            ],
            req=req,
        )
        result = calculate_capacity_from_simulation(req, sim)
        self.assertIsNone(result.earliest_date_for_full_payment)

    def test_optimized_capacity_equals_brute_force(self):
        req = request(amount=Decimal("1000"))
        sim = simulate([event("future", amount=Decimal("300"), event_date=date(2026, 4, 5))], req=req)
        result = calculate_capacity_from_simulation(req, sim)
        safe_values = [
            amount
            for amount in (Decimal("0"), Decimal("100"), Decimal("200"), Decimal("300"), Decimal("400"), Decimal("500"), Decimal("600"))
            if one_time_payment_is_safe(sim, req.request_date, amount)
        ]
        self.assertEqual(max(safe_values), result.amount_safe_to_pay)

    def test_plan_safety_replays_arbitrary_schedule(self):
        req = request(amount=Decimal("800"))
        sim = simulate([event("bill", amount=Decimal("200"), event_date=date(2026, 4, 20))], req=req)
        safe = plan_safety(sim, (RequestPayment(date(2026, 4, 1), Decimal("300")), RequestPayment(date(2026, 4, 10), Decimal("300"))))
        unsafe = plan_safety(sim, (RequestPayment(date(2026, 4, 1), Decimal("500")), RequestPayment(date(2026, 4, 10), Decimal("300"))))
        self.assertTrue(safe.safe)
        self.assertFalse(unsafe.safe)
        self.assertEqual(unsafe.rejection_reasons, ("minimum_balance_breach",))


if __name__ == "__main__":
    unittest.main()
