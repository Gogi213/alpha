"""TK-488 Н5: независимый пересчёт интервалов вселенной. python -m pytest tools/compute/tests/test_tk488_known3.py"""
import importlib.util, os, unittest
from datetime import date, timedelta

spec = importlib.util.spec_from_file_location('k3', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tk488_known3.py'))
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


class T(unittest.TestCase):
    def test_entry_after_median_crosses_and_exit_on_drop(self):
        d0 = date(2026, 9, 1)
        by = {d0 + timedelta(i): v for i, v in enumerate([2e6] * 4 + [0.1e6] * 6)}
        ivs, med = k.expected(by)
        self.assertEqual(med[d0 + timedelta(1)], (2e6, 1))
        # день 4: окно [2,2,2,2]e6 -> входит; день 5: [0.1,2,2,2,2] -> медиана 2e6; день 6: [.1,.1,2,2,2,2] -> 2e6; день 7: [.1,.1,.1,2,2,2,2] -> 2e6; день 8: 4 из 8 -> (0.1+2)/2=1.05e6; день 9: 5 из 9 малых -> 0.1e6
        self.assertEqual(ivs, [(d0 + timedelta(1), d0 + timedelta(8))])

    def test_single_day_not_enough_history_is_empty(self):
        self.assertEqual(k.expected({date(2026, 9, 1): 5e6})[0], [])


if __name__ == '__main__':
    unittest.main()
