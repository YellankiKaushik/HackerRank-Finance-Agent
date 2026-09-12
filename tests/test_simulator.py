import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import Direction, EventStatus, PaymentMethod
from buywait.loaders import load_dataset
from buywait.models import FinancialEvent, FinancialProfile
from buywait.simulator import SimulationPolicy, simulate_baseline_for_request, simulate_baseline_from_events


def profile(balance=Decimal("1000"), minimum=Decimal("100"), currency="USD", protected=()):
    return FinancialProfile(
        user_id="user_x",
        home_currency=currency,
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=(),
        expense_categories_to_protect=tuple(protected),
        expense_categories_user_is_willing_to_reduce=(),
        expense_categories_user_is_willing_to_stop=(),
        payment_methods_user_will_consider=(PaymentMethod.FULL_PAYMENT,),
        max_installment_months=None,
        source_row=2,
    )


def event(
    event_id,
    *,
    user_id="user_x",
    direction=Direction.DEBIT,
    amount=Decimal("100"),
    currency="USD",
    event_date=date(2026, 1, 1),
    settlement_date=None,
    status=EventStatus.SETTLED,
    category="rent",
    event_type="expense",
    description="Apartment rent",
    linked_event_id=None,
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
        currency=currency,
        event_date=event_date,
        settlement_date=settlement_date or event_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility="fixed",
        minimum_allowed_amount=None,
        source_row=source_row,
    )


def simulate(events, *, request_date=date(2026, 4, 1), prof=None, rates=None):
    event_tuple = tuple(events)
    rates = rates or {}

    def lookup(rate_date, source, target):
        return rates[(rate_date, source, target)]

    return simulate_baseline_from_events(
        request_id="request_x",
        request_date=request_date,
        profile=prof or profile(),
        events=event_tuple,
        raw_event_by_id={item.event_id: item for item in event_tuple},
        fx_lookup=lookup,
    )


class SimulatorTests(unittest.TestCase):
    def test_starting_balance_anchor_and_historical_events_not_replayed(self):
        result = simulate([event("old", amount=Decimal("900"), event_date=date(2026, 1, 1))])
        self.assertEqual(result.opening_balance_anchor, Decimal("1000"))
        self.assertEqual(result.days[0].opening_balance, Decimal("1000"))
        self.assertEqual(result.days[0].closing_balance, Decimal("1000"))

    def test_future_scheduled_debit(self):
        result = simulate([event("future", status=EventStatus.SCHEDULED, event_date=date(2026, 4, 10))])
        day = next(item for item in result.days if item.date == date(2026, 4, 10))
        self.assertEqual(day.debits, Decimal("100"))
        self.assertEqual(day.closing_balance, Decimal("900"))

    def test_confirmed_future_credit(self):
        result = simulate(
            [
                event(
                    "payroll",
                    direction=Direction.CREDIT,
                    category="salary",
                    event_type="income",
                    description="Payroll",
                    amount=Decimal("500"),
                    event_date=date(2026, 4, 15),
                )
            ]
        )
        day = next(item for item in result.days if item.date == date(2026, 4, 15))
        self.assertEqual(day.credits, Decimal("500"))
        self.assertEqual(day.closing_balance, Decimal("1500"))

    def test_pending_debit_reserved_and_pending_credit_excluded(self):
        result = simulate(
            [
                event("card", status=EventStatus.PENDING, event_date=date(2026, 3, 30), settlement_date=date(2026, 4, 4)),
                event(
                    "refund",
                    direction=Direction.CREDIT,
                    status=EventStatus.PENDING,
                    amount=Decimal("999"),
                    event_date=date(2026, 4, 2),
                ),
            ]
        )
        self.assertEqual(result.days[0].debits, Decimal("100"))
        self.assertEqual(result.days[0].closing_balance, Decimal("900"))
        self.assertFalse(any(occurrence.source_ids == ("refund",) for occurrence in result.occurrences))

    def test_cancelled_failed_and_unrealized_ignored(self):
        result = simulate(
            [
                event("cancelled", status=EventStatus.CANCELLED, event_date=date(2026, 4, 2)),
                event("failed", status=EventStatus.FAILED, event_date=date(2026, 4, 3)),
                event(
                    "unrealized",
                    status=EventStatus.UNREALIZED,
                    direction=Direction.NON_CASH,
                    amount=Decimal("10000"),
                    event_date=date(2026, 4, 4),
                ),
            ]
        )
        self.assertEqual(result.occurrences, ())

    def test_recurring_fixed_expense_and_income(self):
        events = [
            event("rent1", event_date=date(2026, 1, 5), source_row=2),
            event("rent2", event_date=date(2026, 2, 5), source_row=3),
            event("rent3", event_date=date(2026, 3, 5), source_row=4),
            event("pay1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 1, 10), source_row=5),
            event("pay2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 2, 10), source_row=6),
            event("pay3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 3, 10), source_row=7),
        ]
        result = simulate(events)
        self.assertIn(date(2026, 4, 5), [item.date for item in result.occurrences])
        self.assertIn(date(2026, 4, 10), [item.date for item in result.occurrences])

    def test_conservative_variable_recurrence(self):
        events = [
            event("u1", amount=Decimal("90"), category="utilities", description="Utility bill", event_date=date(2026, 1, 6), source_row=2),
            event("u2", amount=Decimal("125"), category="utilities", description="Utility bill", event_date=date(2026, 2, 6), source_row=3),
            event("u3", amount=Decimal("110"), category="utilities", description="Utility bill", event_date=date(2026, 3, 6), source_row=4),
            event("b1", direction=Direction.CREDIT, amount=Decimal("400"), category="bonus", event_type="income", description="Contract retainer", event_date=date(2026, 1, 8), source_row=5),
            event("b2", direction=Direction.CREDIT, amount=Decimal("350"), category="bonus", event_type="income", description="Contract retainer", event_date=date(2026, 2, 8), source_row=6),
            event("b3", direction=Direction.CREDIT, amount=Decimal("375"), category="bonus", event_type="income", description="Contract retainer", event_date=date(2026, 3, 8), source_row=7),
        ]
        result = simulate(events)
        utility = next(item for item in result.occurrences if item.date == date(2026, 4, 6))
        income = next(item for item in result.occurrences if item.date == date(2026, 4, 8))
        self.assertEqual(utility.amount, Decimal("125"))
        self.assertEqual(income.amount, Decimal("350"))

    def test_explicit_future_event_deduplicates_recurrence(self):
        events = [
            event("r1", event_date=date(2026, 1, 5), source_row=2),
            event("r2", event_date=date(2026, 2, 5), source_row=3),
            event("r3", event_date=date(2026, 3, 5), source_row=4),
            event("r4", status=EventStatus.SCHEDULED, event_date=date(2026, 4, 5), source_row=5),
        ]
        result = simulate(events)
        april_rent = [item for item in result.occurrences if item.date == date(2026, 4, 5)]
        self.assertEqual(len(april_rent), 1)
        self.assertTrue(april_rent[0].explicit)
        self.assertEqual(result.deduplicated_recurring_occurrence_count, 1)

    def test_explicit_future_event_deduplicates_same_cash_identity(self):
        events = [
            event("u1", category="utilities", description="Electricity bill", amount=Decimal("90"), event_date=date(2026, 1, 7), source_row=2),
            event("u2", category="utilities", description="Electricity bill", amount=Decimal("90"), event_date=date(2026, 2, 7), source_row=3),
            event("u3", category="utilities", description="Electricity bill", amount=Decimal("90"), event_date=date(2026, 3, 7), source_row=4),
            event("future", category="utilities", description="Municipal utility payment", amount=Decimal("90"), status=EventStatus.SCHEDULED, event_date=date(2026, 4, 7), source_row=5),
        ]
        result = simulate(events)
        april_utility = [item for item in result.occurrences if item.date == date(2026, 4, 7)]
        self.assertEqual(len(april_utility), 1)
        self.assertTrue(april_utility[0].explicit)
        self.assertEqual(result.deduplicated_recurring_occurrence_count, 1)

    def test_explicit_confirmed_salary_suppresses_earlier_same_stream_inference(self):
        events = [
            event("s1", direction=Direction.CREDIT, category="salary", event_type="income", description="Base salary", amount=Decimal("500"), event_date=date(2026, 1, 15), source_row=2),
            event("s2", direction=Direction.CREDIT, category="salary", event_type="income", description="Base salary", amount=Decimal("500"), event_date=date(2026, 2, 15), source_row=3),
            event("s3", direction=Direction.CREDIT, category="salary", event_type="income", description="Base salary", amount=Decimal("500"), event_date=date(2026, 3, 15), source_row=4),
            event("future", direction=Direction.CREDIT, category="salary", event_type="income", description="Base salary", amount=Decimal("500"), status=EventStatus.SCHEDULED, event_date=date(2026, 5, 15), source_row=5),
        ]
        result = simulate(events)
        self.assertFalse(any(item.date == date(2026, 4, 15) and item.category == "salary" for item in result.occurrences))
        may_salary = [item for item in result.occurrences if item.date == date(2026, 5, 15) and item.category == "salary"]
        self.assertEqual(len(may_salary), 1)
        self.assertTrue(may_salary[0].explicit)
        self.assertEqual(result.deduplicated_recurring_occurrence_count, 2)

    def test_protected_variable_category_forecast_uses_recent_max_before_next_income(self):
        events = [
            event("g1", category="groceries", description="Market produce", amount=Decimal("60"), event_date=date(2026, 1, 5), source_row=2),
            event("g2", category="groceries", description="Pantry refill", amount=Decimal("40"), event_date=date(2026, 1, 12), source_row=3),
            event("g3", category="groceries", description="Neighbourhood grocer", amount=Decimal("30"), event_date=date(2026, 2, 5), source_row=4),
            event("g4", category="groceries", description="Fresh food shop", amount=Decimal("30"), event_date=date(2026, 2, 12), source_row=5),
            event("g5", category="groceries", description="Weekly produce market", amount=Decimal("20"), event_date=date(2026, 3, 5), source_row=6),
            event("g6", category="groceries", description="Bulk pantry shop", amount=Decimal("20"), event_date=date(2026, 3, 12), source_row=7),
            event("s1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 1, 15), source_row=8),
            event("s2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 2, 15), source_row=9),
            event("s3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 3, 15), source_row=10),
        ]
        result = simulate(events, request_date=date(2026, 4, 1), prof=profile(protected=("groceries",)))
        forecast = [item for item in result.occurrences if item.source_type == "variable_essential_forecast"]
        self.assertEqual([(item.date, item.amount) for item in forecast], [(date(2026, 4, 5), Decimal("50.00")), (date(2026, 4, 12), Decimal("50.00"))])
        self.assertTrue(all(item.date < date(2026, 4, 15) for item in forecast))

    def test_fixed_recurring_expense_is_not_double_counted_as_variable_essential(self):
        events = [
            event("r1", category="rent", description="Apartment rent", amount=Decimal("300"), event_date=date(2026, 1, 5), source_row=2),
            event("r2", category="rent", description="Apartment rent", amount=Decimal("300"), event_date=date(2026, 2, 5), source_row=3),
            event("r3", category="rent", description="Apartment rent", amount=Decimal("300"), event_date=date(2026, 3, 5), source_row=4),
            event("s1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 1, 15), source_row=5),
            event("s2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 2, 15), source_row=6),
            event("s3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 3, 15), source_row=7),
        ]
        result = simulate(events, prof=profile(protected=("rent",)))
        self.assertTrue(any(item.category == "rent" and item.source_type == "inferred_recurrence" for item in result.occurrences))
        self.assertFalse(any(item.category == "rent" and item.source_type == "variable_essential_forecast" for item in result.occurrences))

    def test_variable_forecast_ignores_events_after_request_date(self):
        events = [
            event("g1", category="groceries", description="Market produce", amount=Decimal("20"), event_date=date(2026, 1, 5), source_row=2),
            event("g2", category="groceries", description="Pantry refill", amount=Decimal("20"), event_date=date(2026, 2, 5), source_row=3),
            event("g3", category="groceries", description="Household groceries", amount=Decimal("20"), event_date=date(2026, 3, 5), source_row=4),
            event("future_grocery", category="groceries", description="Future grocery shop", amount=Decimal("999"), event_date=date(2026, 4, 2), source_row=5),
            event("s1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 1, 15), source_row=6),
            event("s2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 2, 15), source_row=7),
            event("s3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 3, 15), source_row=8),
        ]
        result = simulate(events, request_date=date(2026, 4, 1), prof=profile(protected=("groceries",)))
        forecast = [item for item in result.occurrences if item.source_type == "variable_essential_forecast"]
        self.assertEqual([item.amount for item in forecast], [Decimal("20.00")])

    def test_variable_forecast_is_row_order_independent(self):
        events = [
            event("g1", category="groceries", description="Market produce", amount=Decimal("60"), event_date=date(2026, 1, 5), source_row=2),
            event("g2", category="groceries", description="Pantry refill", amount=Decimal("40"), event_date=date(2026, 1, 12), source_row=3),
            event("g3", category="groceries", description="Market produce", amount=Decimal("20"), event_date=date(2026, 3, 5), source_row=4),
            event("g4", category="groceries", description="Pantry refill", amount=Decimal("20"), event_date=date(2026, 3, 12), source_row=5),
            event("s1", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 1, 15), source_row=6),
            event("s2", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 2, 15), source_row=7),
            event("s3", direction=Direction.CREDIT, category="salary", event_type="income", description="Payroll", amount=Decimal("500"), event_date=date(2026, 3, 15), source_row=8),
        ]
        first = simulate(events, prof=profile(protected=("groceries",)))
        second = simulate(list(reversed(events)), prof=profile(protected=("groceries",)))
        self.assertEqual(first.days, second.days)
        self.assertEqual(first.occurrences, second.occurrences)

    def test_fx_conversion_for_future_event(self):
        result = simulate(
            [
                event(
                    "eur_bill",
                    amount=Decimal("100"),
                    currency="EUR",
                    status=EventStatus.SCHEDULED,
                    event_date=date(2026, 4, 7),
                )
            ],
            rates={(date(2026, 4, 7), "EUR", "USD"): Decimal("1.20")},
        )
        day = next(item for item in result.days if item.date == date(2026, 4, 7))
        self.assertEqual(day.debits, Decimal("120.00"))
        self.assertEqual(result.fx_conversion_count, 1)

    def test_minimum_balance_headroom_and_baseline_deficit(self):
        result = simulate([event("bill", amount=Decimal("950"), status=EventStatus.SCHEDULED, event_date=date(2026, 4, 2))])
        self.assertEqual(result.minimum_projected_balance, Decimal("50"))
        self.assertEqual(result.minimum_projected_balance_date, date(2026, 4, 2))
        self.assertEqual(result.minimum_headroom, Decimal("-50"))
        self.assertFalse(result.baseline_safe)

    def test_suffix_minimum_headroom(self):
        result = simulate(
            [
                event("small", amount=Decimal("100"), status=EventStatus.SCHEDULED, event_date=date(2026, 4, 2)),
                event("large", amount=Decimal("300"), status=EventStatus.SCHEDULED, event_date=date(2026, 4, 5)),
            ]
        )
        self.assertEqual(result.suffix_min_headroom[date(2026, 4, 1)], Decimal("500"))
        self.assertEqual(result.suffix_min_headroom[date(2026, 4, 5)], Decimal("500"))

    def test_deterministic_under_shuffled_input_ordering(self):
        events = [
            event("r1", event_date=date(2026, 1, 5), source_row=2),
            event("r2", event_date=date(2026, 2, 5), source_row=3),
            event("r3", event_date=date(2026, 3, 5), source_row=4),
            event("future", status=EventStatus.SCHEDULED, event_date=date(2026, 4, 9), source_row=5),
        ]
        first = simulate(events)
        second = simulate(list(reversed(events)))
        self.assertEqual(first.days, second.days)
        self.assertEqual(first.occurrences, second.occurrences)

    def test_month_boundary_recurrence_through_simulator(self):
        events = [
            event("m1", event_date=date(2025, 12, 31), source_row=2),
            event("m2", event_date=date(2026, 1, 31), source_row=3),
            event("m3", event_date=date(2026, 2, 28), source_row=4),
        ]
        result = simulate(events, request_date=date(2026, 3, 2))
        self.assertEqual(
            [item.date for item in result.occurrences[:3]],
            [date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)],
        )

    def test_horizon_boundary_is_inclusive(self):
        result = simulate(
            [event("boundary", status=EventStatus.SCHEDULED, event_date=date(2026, 6, 30))],
            request_date=date(2026, 4, 1),
        )
        self.assertEqual(result.horizon_end, date(2026, 6, 30))
        self.assertTrue(any(item.source_ids == ("boundary",) for item in result.occurrences))

    def test_linked_cancellation_is_not_counted(self):
        events = [
            event("original", status=EventStatus.SCHEDULED, event_date=date(2026, 4, 10), source_row=2),
            event("cancel", status=EventStatus.CANCELLED, event_date=date(2026, 4, 11), linked_event_id="original", source_row=3),
        ]
        result = simulate(events)
        self.assertEqual(result.occurrences, ())


class RealDataSimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_real_sample_and_evaluation_requests_simulate(self):
        simulations = [
            simulate_baseline_for_request(self.dataset, sample.request)
            for sample in self.dataset.sample_requests
        ]
        simulations.extend(simulate_baseline_for_request(self.dataset, request) for request in self.dataset.requests)
        self.assertEqual(len(simulations), 275)
        self.assertTrue(all(result.days for result in simulations))
        self.assertTrue(all(result.days[0].opening_balance == result.opening_balance_anchor for result in simulations))


if __name__ == "__main__":
    unittest.main()
