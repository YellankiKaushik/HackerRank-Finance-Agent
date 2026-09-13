import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.capacity import RequestPayment, plan_safety
from buywait.enums import AffordabilityStatus, PaymentMethod
from buywait.loaders import load_dataset
from buywait.planner import (
    SpendingChange,
    apply_spending_changes,
    decide_request,
    format_payment_plan,
    generate_candidates,
    row_to_csv_dict,
)
from buywait.simulator import simulate_baseline_for_request


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


if __name__ == "__main__":
    unittest.main()
