import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from buywait.errors import ParseError
from buywait.money import format_decimal_compact, format_decimal_plain, parse_money, parse_optional_money


class MoneyTests(unittest.TestCase):
    def test_parse_valid_money(self):
        cases = {
            "0": Decimal("0"),
            "42": Decimal("42"),
            "12.34": Decimal("12.34"),
            "46018000": Decimal("46018000"),
            "620.40": Decimal("620.40"),
            "-5.25": Decimal("-5.25"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_money(raw), expected)

    def test_blank_is_none_for_optional_and_error_for_required(self):
        self.assertIsNone(parse_optional_money(""))
        self.assertIsNone(parse_optional_money(None))
        with self.assertRaises(ParseError):
            parse_money("")

    def test_malformed_money_fails(self):
        for raw in ["abc", "1,000", "12.3.4", " 12"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ParseError):
                    parse_money(raw)

    def test_format_helpers_are_generic(self):
        self.assertEqual(format_decimal_plain(Decimal("620.40")), "620.40")
        self.assertEqual(format_decimal_compact(Decimal("620.40")), "620.4")


if __name__ == "__main__":
    unittest.main()

