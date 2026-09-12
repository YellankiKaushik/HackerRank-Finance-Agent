import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import PaymentMethod
from buywait.loaders import load_dataset
from buywait.planner import decide_request
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


if __name__ == "__main__":
    unittest.main()
