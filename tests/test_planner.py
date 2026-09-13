import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.capacity import RequestPayment, plan_safety
from buywait.enums import AffordabilityStatus, PaymentMethod
from buywait.loaders import load_dataset
from buywait.planner import (
    SpendingChange,
    _candidate,
    add_calendar_months,
    apply_spending_changes,
    decide_request,
    format_payment_plan,
    generate_candidates,
    row_to_csv_dict,
)
from buywait.simulator import BaselineSimulation, DailyBalance, simulate_baseline_for_request


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def decision(self, request_id):
        sample = self.dataset.sample_request_by_id[request_id]
        return decide_request(self.dataset, sample.request)

    def test_full_payment_and_status_mapping(self):
        row = self.decision("request_01")
        self.assertEqual(row.affordability_status, AffordabilityStatus.AFFORDABLE_NOW)
        self.assertEqual(row.recommended_payment_method, PaymentMethod.FULL_PAYMENT)
        self.assertEqual(row.payment_plan, "2024-03-03:25256")

    def test_wait_candidate(self):
        row = self.decision("request_04")
        self.assertEqual(row.affordability_status, AffordabilityStatus.AFFORDABLE_LATER)
        self.assertEqual(row.recommended_payment_method, PaymentMethod.WAIT)
        self.assertEqual(row.payment_plan, "2024-06-15:12693000")

    def test_partial_payment_exact_two_payments(self):
        row = self.decision("request_19")
        self.assertEqual(row.recommended_payment_method, PaymentMethod.PARTIAL_PAYMENT)
        self.assertEqual(len(row.candidate.payments), 2)  # type: ignore[union-attr]
        self.assertEqual(sum((payment.amount for payment in row.candidate.payments), Decimal("0")), Decimal("39660"))  # type: ignore[union-attr]

    def test_installment_option_fidelity_and_financing_fee(self):
        row = self.decision("request_02")
        self.assertEqual(row.recommended_payment_method, PaymentMethod.INSTALLMENTS)
        self.assertEqual(row.payment_plan, "2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67")
        self.assertEqual(row.candidate.total_payable, Decimal("47858720.01"))  # type: ignore[union-attr]

    def test_preference_and_deadline_filtering(self):
        sample = self.dataset.sample_request_by_id["request_06"]
        baseline = simulate_baseline_for_request(self.dataset, sample.request)
        from buywait.capacity import calculate_capacity_for_request

        capacity = calculate_capacity_for_request(self.dataset, sample.request)
        candidates = generate_candidates(self.dataset, sample.request, baseline, capacity)
        rejected_installments = [item for item in candidates if item.method is PaymentMethod.INSTALLMENTS]
        self.assertTrue(rejected_installments)
        self.assertTrue(all(not item.preference_eligible for item in rejected_installments))
        self.assertTrue(all(not item.deadline_eligible for item in rejected_installments))

    def test_spending_stop_and_reduction_apply_to_future_occurrences(self):
        sample = self.dataset.sample_request_by_id["request_21"]
        profile = self.dataset.profile_by_user[sample.request.user_id]
        baseline = simulate_baseline_for_request(self.dataset, sample.request)
        adjusted = apply_spending_changes(
            baseline,
            (
                SpendingChange("stop", "event_1815"),
                SpendingChange("reduce_to", "event_1816", Decimal("23.5")),
            ),
            profile,
        )
        safety = plan_safety(adjusted, (RequestPayment(sample.request.request_date, sample.request.requested_amount),))
        self.assertTrue(safety.safe)
        self.assertFalse(any("event_1815" in occurrence.source_ids for occurrence in adjusted.occurrences))

    def test_ranking_prefers_no_spending_changes_and_lower_total(self):
        row = self.decision("request_01")
        self.assertEqual(row.recommended_payment_method, PaymentMethod.FULL_PAYMENT)
        self.assertEqual(row.candidate.total_payable, Decimal("25256"))  # type: ignore[union-attr]
        self.assertEqual(row.spending_changes_needed, "none")

    def test_output_formatting(self):
        text = format_payment_plan(
            (
                RequestPayment(self.dataset.sample_request_by_id["request_01"].request.request_date, Decimal("10.00")),
                RequestPayment(self.dataset.sample_request_by_id["request_01"].request.request_date, Decimal("2.50")),
            )
        )
        self.assertEqual(text, "2024-03-03:10|2024-03-03:2.50")
        row = row_to_csv_dict(self.decision("request_01"))
        self.assertEqual(list(row), [
            "request_id",
            "amount_safe_to_pay",
            "affordability_status",
            "recommended_payment_method",
            "payment_plan",
            "earliest_date_for_full_payment",
            "spending_changes_needed",
            "decision_explanation",
        ])

    def test_candidate_safety_uses_strict_zero_headroom_boundary(self):
        sample = self.dataset.sample_request_by_id["request_01"]
        request = sample.request
        profile = self.dataset.profile_by_user[request.user_id]
        payment = RequestPayment(request.request_date, request.requested_amount)
        for headroom, expected in (
            (Decimal("0"), True),
            (Decimal("0.01"), True),
            (Decimal("-0.01"), False),
            (Decimal("-5"), False),
        ):
            with self.subTest(headroom=headroom):
                closing = profile.minimum_balance_to_keep + request.requested_amount + headroom
                day = DailyBalance(
                    date=request.request_date,
                    opening_balance=closing,
                    credits=Decimal("0"),
                    debits=Decimal("0"),
                    closing_balance=closing,
                    minimum_balance_to_keep=profile.minimum_balance_to_keep,
                    headroom=closing - profile.minimum_balance_to_keep,
                    occurrences=(),
                )
                baseline = BaselineSimulation(
                    request_id=request.request_id,
                    user_id=request.user_id,
                    request_date=request.request_date,
                    horizon_end=request.request_date,
                    opening_balance_anchor=closing,
                    days=(day,),
                    occurrences=(),
                    suffix_min_headroom={request.request_date: day.headroom},
                    lifecycle_unresolved_count=0,
                    explicit_future_event_count=0,
                    inferred_recurring_occurrence_count=0,
                    deduplicated_recurring_occurrence_count=0,
                    skipped_missing_amount_event_count=0,
                    skipped_missing_amount_event_ids=(),
                    resolved_image_amounts=(),
                    message_facts_applied=(),
                    fx_conversion_count=0,
                )
                candidate = _candidate(
                    request,
                    baseline,
                    method=PaymentMethod.FULL_PAYMENT,
                    payments=(payment,),
                )
                self.assertEqual(candidate.financially_safe, expected)

    def test_add_calendar_months_handles_month_boundaries(self):
        self.assertEqual(add_calendar_months(date(2026, 1, 15), 1), date(2026, 2, 15))
        self.assertEqual(add_calendar_months(date(2025, 1, 29), 1), date(2025, 2, 28))
        self.assertEqual(add_calendar_months(date(2024, 1, 29), 1), date(2024, 2, 29))
        self.assertEqual(add_calendar_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_calendar_months(date(2026, 3, 31), 1), date(2026, 4, 30))

    def test_installment_max_month_boundary_uses_calendar_months(self):
        sample = self.dataset.sample_request_by_id["request_02"]
        profile = self.dataset.profile_by_user[sample.request.user_id]
        start = date(2026, 1, 31)
        limit = add_calendar_months(start, profile.max_installment_months or 0)
        self.assertLessEqual(limit, add_calendar_months(start, profile.max_installment_months or 0))
        self.assertGreater(limit + timedelta(days=1), limit)


if __name__ == "__main__":
    unittest.main()
