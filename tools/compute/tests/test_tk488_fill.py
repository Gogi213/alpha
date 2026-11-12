"""TK-488 (возврат Судьи Н1+Н2): окна докачки свечей REST и разбор строк Gate. Запуск: python -m pytest tools/compute/tests/test_tk488_fill.py"""
import gzip, importlib.util, os, tempfile, unittest
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488f', os.path.join(HERE, '..', 'tk488_fill.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def minutes(a, b):
    return list(range(a, b + 1, m.MIN))


class T(unittest.TestCase):
    def test_tail_with_overlap_binance_without_gate(self):
        ts = minutes(ms(2026, 10, 30), ms(2026, 10, 31, 23, 59))
        last = ms(2026, 11, 2, 23, 59)
        self.assertEqual(m.windows(ts, last, [], True), [(ms(2026, 10, 31, 23, 59), last)])
        self.assertEqual(m.windows(ts, last, [], False), [(ms(2026, 11, 1), last)])

    def test_up_to_date_no_windows(self):
        ts = minutes(ms(2026, 10, 30), ms(2026, 11, 2, 23, 59))
        self.assertEqual(m.windows(ts, ms(2026, 11, 2, 23, 59), [], True), [])
        self.assertEqual(m.windows([], ms(2026, 11, 2, 23, 59), [], True), [])

    def test_gap_day_only_when_day_has_no_rows(self):
        d = date(2026, 10, 28)
        full = minutes(ms(2026, 10, 27), ms(2026, 10, 27, 23, 59)) + minutes(ms(2026, 10, 29), ms(2026, 11, 2, 23, 59))
        last = ms(2026, 11, 2, 23, 59)
        self.assertEqual(m.windows(full, last, [d], True), [(ms(2026, 10, 28), ms(2026, 10, 28, 23, 59))])
        part = full + [ms(2026, 10, 28, 5)]  # в сутках уже есть строка — не трогаем
        self.assertEqual(m.windows(sorted(part), last, [d], True), [])
        late = minutes(ms(2026, 10, 30), last)  # контракт начал торговаться позже дыры
        self.assertEqual(m.windows(late, last, [d], True), [])
        old = minutes(ms(2026, 10, 1), ms(2026, 10, 20, 23, 59))  # снят до дыры
        self.assertEqual(m.windows(old, ms(2026, 10, 20, 23, 59), [d], True), [])

    def test_gate_rows_skip_empty_minutes_and_scale_by_quanto(self):
        calls = []

        def fake(url, gap, tries=8):
            calls.append(url)
            if '/contracts/' in url:
                return {'quanto_multiplier': '0.01'}
            return [{'t': 1793404860, 'o': '1', 'h': '2', 'l': '0.5', 'c': '1.5', 'v': 250, 'sum': '3.75'},
                    {'t': 1793404800, 'o': '1', 'h': '1', 'l': '1', 'c': '1', 'v': 0, 'sum': '0'}]
        old, m.get_json = m.get_json, fake
        try:
            rows = m.fetch_gate('X_USDT', 1793404800000, 1793404860000, 0)
        finally:
            m.get_json = old
        self.assertEqual(rows, ['1793404860000,1,2,0.5,1.5,2.5,3.75,\n'])

    def test_one_writes_stage_and_skips_when_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f'{d}/root/binance-um')
            last = ms(2026, 11, 2, 23, 59)
            with gzip.open(f'{d}/root/binance-um/A.csv.gz', 'wt') as g:
                g.write(m.HDR)
                for t in minutes(ms(2026, 11, 2), last - m.MIN):
                    g.write(f'{t},1,1,1,1,1,1,1\n')
            m.FETCH['binance-um'] = (lambda s, a, b, gap: [f'{a},1,1,1,1,1,1,1\n', f'{last},2,2,2,2,2,2,2\n'], 0, True)
            r = m.one('binance-um', 'A', last, [], f'{d}/root', f'{d}/st')
            self.assertEqual((r[2], r[3]), (2, ''))
            with gzip.open(f'{d}/st/binance-um/A.csv.gz', 'rt') as f:
                self.assertEqual(len(f.read().splitlines()), 3)
            self.assertEqual(m.one('binance-um', 'B', last, [], f'{d}/root', f'{d}/st')[3], 'нет лежащего файла (не дельта)')


if __name__ == '__main__':
    unittest.main()
