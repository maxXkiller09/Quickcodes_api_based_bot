"""Unit tests for back-in-stock transition detection."""
import unittest

from stock_alerts import is_restock_transition


class RestockTransitionTests(unittest.TestCase):
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
