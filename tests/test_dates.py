import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.dates import parse_date, parse_iso_timestamp, parse_optional_date
from buywait.errors import ParseError


class DateTests(unittest.TestCase):
    def test_parse_valid_dates(self):
        self.assertEqual(parse_date("2026-09-12"), date(2026, 9, 12))
        self.assertEqual(parse_date("2024-02-29"), date(2024, 2, 29))

    def test_invalid_or_blank_dates_fail(self):
        for raw in ["", "2023-02-29", "20260912", "2026-9-12"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ParseError):
                    parse_date(raw)

    def test_optional_date(self):
        self.assertIsNone(parse_optional_date(""))
        self.assertEqual(parse_optional_date("2025-01-15"), date(2025, 1, 15))

    def test_iso_timestamps(self):
        parsed = parse_iso_timestamp("2025-12-28T09:30:00Z")
        self.assertEqual(parsed.year, 2025)
        self.assertEqual(parsed.minute, 30)
        with self.assertRaises(ParseError):
            parse_iso_timestamp("")


if __name__ == "__main__":
    unittest.main()

