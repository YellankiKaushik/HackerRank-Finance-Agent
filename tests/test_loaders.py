import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.enums import PaymentMethod, RequestType
from buywait.errors import SchemaError
from buywait.loaders import REQUEST_COLUMNS, load_dataset, load_requests


class LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "dataset")

    def test_real_dataset_loads(self):
        self.assertEqual(len(self.dataset.requests), 250)
        self.assertEqual(len(self.dataset.sample_requests), 25)
        self.assertEqual(len(self.dataset.profiles), 275)
        self.assertEqual(len(self.dataset.events), 25342)
        self.assertEqual(len(self.dataset.payment_options), 790)
        self.assertEqual(len(self.dataset.messages), 215)
        self.assertEqual(len(self.dataset.images), 16)
        self.assertEqual(len(self.dataset.exchange_rates), 134)

    def test_request_types_and_money_are_typed(self):
        request = self.dataset.requests[0]
        self.assertIsInstance(request.requested_amount, Decimal)
        self.assertIsInstance(request.request_type, RequestType)

    def test_profile_payment_methods_are_typed(self):
        profile = self.dataset.profile_by_user["user_12"]
        self.assertIn(PaymentMethod.INSTALLMENTS, profile.payment_methods_user_will_consider)

    def test_header_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(REQUEST_COLUMNS[:-1])
            with self.assertRaises(SchemaError):
                load_requests(Path(tmp))


if __name__ == "__main__":
    unittest.main()

