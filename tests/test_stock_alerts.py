"""Unit tests for back-in-stock transition detection."""
import unittest

from stock_alerts import is_restock_transition, poll_delay, format_stock_count, format_inr


class RestockTransitionTests(unittest.TestCase):
    def test_stock_count_uses_thousands_separators(self):
        self.assertEqual(format_stock_count(1054684), "1,054,684")

    def test_price_formats_scientific_notation_as_inr(self):
        self.assertEqual(format_inr(3.26747e6), "32,67,470.00")

    def test_price_formats_decimal_without_changing_value(self):
        self.assertEqual(format_inr("3267470.5"), "32,67,470.50")

    def test_poll_runs_every_five_minutes_start_to_start(self):
        self.assertEqual(poll_delay(300, 0), 300)
        self.assertEqual(poll_delay(300, 90), 210)

    def test_slow_poll_does_not_add_another_five_minute_wait(self):
        self.assertEqual(poll_delay(300, 360), 0)

    def test_poll_interval_never_falls_below_five_minutes(self):
        self.assertEqual(poll_delay(60, 10), 290)

    def test_first_snapshot_does_not_trigger(self):
        self.assertFalse(is_restock_transition(None, 5))

    def test_zero_to_positive_triggers(self):
        self.assertTrue(is_restock_transition(0, 3))

    def test_positive_to_positive_does_not_trigger(self):
        self.assertFalse(is_restock_transition(2, 5))

    def test_zero_to_zero_does_not_trigger(self):
        self.assertFalse(is_restock_transition(0, 0))

    def test_positive_to_zero_does_not_trigger(self):
        self.assertFalse(is_restock_transition(4, 0))

    def test_invalid_values_do_not_trigger(self):
        self.assertFalse(is_restock_transition("unknown", 3))


if __name__ == "__main__":
    unittest.main()
