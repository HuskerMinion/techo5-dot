"""Tests for the parts of install-dot.py that need no device. Run: python3 -m unittest tools/test_install_dot.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('install_dot', os.path.join(HERE, 'install-dot.py'))
install_dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_dot)


class RescueDetection(unittest.TestCase):
    def test_rescue_when_no_slot_and_five_tries(self):
        self.assertTrue(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 5 unhealthy'}))
        self.assertTrue(install_dot.in_rescue({'tries': 'TECHO5-TRIES 7 x'}))

    def test_not_rescue_with_a_slot(self):
        self.assertFalse(install_dot.in_rescue({'slot': 'a', 'tries': 'TECHO5-TRIES 5 x'}))

    def test_not_rescue_below_five_or_unknown(self):
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 2 booting'}))
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': ''}))
        self.assertFalse(install_dot.in_rescue({}))
        self.assertFalse(install_dot.in_rescue({'slot': '', 'tries': 'TECHO5-TRIES 0 healthy'}))


if __name__ == '__main__':
    unittest.main()
