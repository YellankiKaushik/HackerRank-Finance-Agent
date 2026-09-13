import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import AffordabilityStatus, PaymentMethod
from buywait.loaders import load_dataset
from buywait.planner import DecisionRow, decide_request
from buywait.simulator import BaselineSimulation, DailyBalance
from buywait.verifier import verify_decision


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_accepts_verified_planner_row(self):
        sample = self.dataset.sample_request_by_id["request_02"]
        row = decide_request(self.dataset, sample.request)
        result = verify_decision(self.dataset, sample.request, row)
        self.assertTrue(result.valid, result.errors)

    def test_rejects_installment_schedule_that_does_not_match_option(self):
        sample = self.dataset.sample_request_by_id["request_02"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, payment_plan=row.payment_plan.replace("15952906.67", "15952906.68", 1))
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("installment_option_mismatch", result.errors)

    def test_rejects_unpreferred_method(self):
        sample = self.dataset.sample_request_by_id["request_06"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, recommended_payment_method=PaymentMethod.INSTALLMENTS, payment_plan="2026-01-10:61.01|2026-02-09:61.01")
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("method_not_preferred", result.errors)

    def test_rejects_too_many_spending_changes(self):
        sample = self.dataset.sample_request_by_id["request_21"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(
            row,
            spending_changes_needed="stop:event_1815|reduce_to:event_1816:23.5|reduce_to:event_1817:49.6|stop:event_1815",
        )
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("too_many_spending_changes", result.errors)

    def test_rejects_reduce_below_minimum_allowed_amount(self):
        sample = self.dataset.sample_request_by_id["request_21"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, spending_changes_needed="reduce_to:event_1816:0")
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("below_minimum_allowed_amount", result.errors)

    def test_rejects_payment_total_mismatch(self):
        sample = self.dataset.sample_request_by_id["request_01"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, payment_plan="2024-03-03:1")
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("invalid_full_payment", result.errors)

    def test_verifier_strictly_rejects_negative_headroom(self):
        sample = self.dataset.sample_request_by_id["request_01"]
        request = sample.request
        profile = self.dataset.profile_by_user[request.user_id]
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
                row = DecisionRow(
                    request_id=request.request_id,
                    amount_safe_to_pay=request.requested_amount,
                    affordability_status=AffordabilityStatus.AFFORDABLE_NOW,
                    recommended_payment_method=PaymentMethod.FULL_PAYMENT,
                    payment_plan=f"{request.request_date.isoformat()}:{request.requested_amount}",
                    earliest_date_for_full_payment=request.request_date,
                    spending_changes_needed="none",
                    decision_explanation="Test row.",
                    candidate=None,
                )
                result = verify_decision(self.dataset, request, row, baseline=baseline)
                self.assertEqual(result.valid, expected, result.errors)
                if not expected:
                    self.assertIn("minimum_balance_breach", result.errors)

    def test_rejects_partial_payment_that_violates_exact_rule(self):
        sample = self.dataset.sample_request_by_id["request_19"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, payment_plan="2024-09-04:28819|2024-09-15:10841")
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("partial_first_amount", result.errors)
        self.assertIn("partial_second_amount", result.errors)

    def test_rejects_partial_payment_missing_earliest_date(self):
        sample = self.dataset.sample_request_by_id["request_19"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, earliest_date_for_full_payment=None)
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("partial_missing_earliest_full_date", result.errors)

    def test_rejects_inconsistent_status_and_method(self):
        sample = self.dataset.sample_request_by_id["request_01"]
        row = decide_request(self.dataset, sample.request)
        broken = replace(row, affordability_status=AffordabilityStatus.AFFORDABLE_LATER)
        result = verify_decision(self.dataset, sample.request, broken)
        self.assertFalse(result.valid)
        self.assertIn("affordable_later_requires_wait", result.errors)

    def test_rejects_installments_when_max_months_blank(self):
        sample = self.dataset.sample_request_by_id["request_06"]
        request = sample.request
        option = next(
            item
            for item in self.dataset.payment_options_by_request[request.request_id]
            if item.payment_method is PaymentMethod.INSTALLMENTS
        )
        payments = []
        current = option.first_payment_date
        for idx in range(option.number_of_payments):
            payments.append(f"{current.isoformat()}:{option.payment_amount}")
            if idx < option.number_of_payments - 1:
                current = date.fromordinal(current.toordinal() + (option.payment_frequency_days or 0))
        row = DecisionRow(
            request_id=request.request_id,
            amount_safe_to_pay=Decimal("0"),
            affordability_status=AffordabilityStatus.AFFORDABLE_WITH_PLAN,
            recommended_payment_method=PaymentMethod.INSTALLMENTS,
            payment_plan="|".join(payments),
            earliest_date_for_full_payment=None,
            spending_changes_needed="none",
            decision_explanation="Test row.",
            candidate=None,
        )
        result = verify_decision(self.dataset, request, row)
        self.assertFalse(result.valid)
        self.assertIn("installments_without_max_months", result.errors)


if __name__ == "__main__":
    unittest.main()
