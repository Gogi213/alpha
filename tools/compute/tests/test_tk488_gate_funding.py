"""TK-488 Н3: Gate-фандинг — страницы от конца назад до пустой, снятый контракт (400) не выдумывается. python -m pytest tools/compute/tests/test_tk488_gate_funding.py"""
import gzip, importlib.util, os, tempfile, unittest

spec = importlib.util.spec_from_file_location('gf', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tk488_gate_funding.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class T(unittest.TestCase):
    def test_pages_back_until_empty_and_absent(self):
        data = [{'t': 1700000000 + 28800 * i, 'r': str(i)} for i in range(5)]

        def fake(url, gap, tries=8):
            if 'NOPE' in url:
                return None
            to = int(url.split('to=')[1].split('&')[0])
            return sorted([x for x in data if x['t'] <= to], key=lambda x: -x['t'])[:2]  # страница по 2 записи
        old, g.f.get_json = g.f.get_json, fake
        try:
            self.assertEqual(sorted(t for t, _ in g.pages('X_USDT', 2 * 10 ** 9)), [x['t'] for x in data])
            self.assertIsNone(g.pages('NOPE_USDT', 2 * 10 ** 9))
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(g.one('X_USDT', d, 2 * 10 ** 9), ('X_USDT', 'ок', 5))
                self.assertEqual(g.one('X_USDT', d, 2 * 10 ** 9), ('X_USDT', 'есть', 0))  # готовый не переписывается
                with gzip.open(os.path.join(d, g.EX, 'X_USDT.csv.gz'), 'rt') as fh:
                    rows = fh.read().splitlines()
                self.assertEqual(rows[1], '1700000000000,0,8.0')
        finally:
            g.f.get_json = old


if __name__ == '__main__':
    unittest.main()
