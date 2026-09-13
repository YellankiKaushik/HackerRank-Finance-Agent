import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.capacity import calculate_capacity_for_request
from buywait.loaders import load_dataset


class PublicCapacityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_public_capacity_current_baseline(self):
        rows = []
        safe_matches = 0
        earliest_matches = 0
        for sample in self.dataset.sample_requests:
            result = calculate_capacity_for_request(self.dataset, sample.request)
            expected = sample.expected
            safe_ok = result.amount_safe_to_pay == expected.amount_safe_to_pay
            earliest_ok = result.earliest_date_for_full_payment == expected.earliest_date_for_full_payment
            safe_matches += int(safe_ok)
            earliest_matches += int(earliest_ok)
            if not safe_ok or not earliest_ok:
                rows.append(
                    (
                        sample.request.request_id,
                        expected.amount_safe_to_pay,
                        result.amount_safe_to_pay,
                        result.amount_safe_to_pay - expected.amount_safe_to_pay,
                        expected.earliest_date_for_full_payment,
                        result.earliest_date_for_full_payment,
                    )
                )

        self.assertEqual(safe_matches, 4, self._format_mismatches(rows))
        self.assertEqual(earliest_matches, 14, self._format_mismatches(rows))

    def _format_mismatches(self, rows):
        header = "request_id expected_safe actual_safe safe_delta expected_earliest actual_earliest"
        body = "\n".join(" ".join(str(part) for part in row) for row in rows)
        return f"\n{header}\n{body}"


if __name__ == "__main__":
    unittest.main()
