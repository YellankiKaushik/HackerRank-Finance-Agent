import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.errors import IntegrityError
from buywait.loaders import load_dataset


class DatasetIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_indexes_are_built(self):
        self.assertIn("user_01", self.dataset.profile_by_user)
        self.assertIn("request_26", self.dataset.request_by_id)
        self.assertIn("event_01", self.dataset.event_by_id)
        self.assertGreater(len(self.dataset.events_by_user["user_01"]), 0)
        self.assertGreater(len(self.dataset.payment_options_by_request["request_01"]), 0)

    def test_all_image_paths_resolve(self):
        for image in self.dataset.images:
            path = self.dataset.require_image_path(image.image_id)
            self.assertTrue(path.exists(), image.image_id)
            self.assertEqual(path.suffix, ".png")

    def test_fx_lookup_uses_exact_direction_only(self):
        rate = self.dataset.exchange_rates[0]
        self.assertIs(
            self.dataset.require_exchange_rate(rate.rate_date, rate.from_currency, rate.to_currency),
            rate,
        )
        with self.assertRaises(IntegrityError):
            self.dataset.require_exchange_rate(date(1900, 1, 1), "USD", "EUR")

    def test_counts_report(self):
        counts = self.dataset.counts()
        self.assertEqual(counts["requests"], 250)
        self.assertEqual(counts["images"], 16)


if __name__ == "__main__":
    unittest.main()
