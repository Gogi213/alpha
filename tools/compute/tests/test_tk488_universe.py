"""TK-488: вселенная TradFi с порогом 1 млн на всех биржах (tk488_universe.py), офлайн, без сети (--no-fetch).
Проверяет: порог 1 млн вместо 5 млн у Binance, точка-во-времени (медиана 30 пред. суток), крипто-индекс и котировка не USDT
не входят, контракт без оборота уходит в no-turnover, недостающие свечи (нет файла / до первой / после последней)."""
import csv, gzip, importlib.util, os, shutil, sys, tempfile, unittest
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488u', os.path.join(HERE, '..', 'tk488_universe.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

D0 = date(2026, 9, 1)
LIST_HDR = ['exchange', 'symbol', 'class', 'class_src', 'subtype', 'contract_type', 'status', 'onboard_ms', 'meta_source', 'flag']


def lrow(ex, sym, cls='equity', flag=''):
    return [ex, sym, cls, 'X', '', 'P', 'TRADING', '1', 'm', flag]


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.uni = os.path.join(self.d, 'uni')
        os.makedirs(self.uni)

    def tearDown(self):
        shutil.rmtree(self.d)

    def turnover(self, ex, series):
        with gzip.open(os.path.join(self.uni, f'turnover-{ex}.csv.gz'), 'wt', newline='') as f:
            w = csv.writer(f)
            w.writerow(['exchange', 'symbol', 'day', 'turnover_usdt', 'med30_prev', 'n30_prev'])
            for sym, vals in series.items():
                for i, v in enumerate(vals):
                    w.writerow([ex, sym, (D0 + timedelta(i)).isoformat(), v, '', 0])

    def run_all(self, rows, cov, fetch=False):
        lst = os.path.join(self.d, 'list.csv')
        with open(lst, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(LIST_HDR)
            w.writerows(rows)
        cf = os.path.join(self.d, 'cov.csv')
        with open(cf, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['биржа', 'символ', 'первая_utc', 'последняя_utc'])
            w.writerows(cov)
        out = os.path.join(self.d, 'out')
        sys.argv = ['x', lst, self.uni, cf, out] + ([] if fetch else ['--no-fetch'])
        m.main()
        return out

    def read(self, out, name):
        return list(csv.DictReader(open(os.path.join(out, name), encoding='utf-8')))

    def test_threshold_1m_not_5m_binance(self):
        # 3 млн в сутки: у крипто Binance (5 млн) не прошёл бы, у TradFi (1 млн) проходит со 2-го дня (медиана пред. окна)
        for ex in m.EXCH:
            self.turnover(ex, {})
        self.turnover('binance-um', {'TSLAUSDT': [3e6] * 5, 'LOWUSDT': [0.5e6] * 5})
        out = self.run_all([lrow('binance-um', 'TSLAUSDT'), lrow('binance-um', 'LOWUSDT')], [])
        uni = self.read(out, 'universe-binance-um.csv')
        self.assertEqual([(r['symbol'], r['from_day'], r['to_day']) for r in uni], [('TSLAUSDT', '2026-09-02', '2026-09-05')])

    def test_excluded_and_no_turnover(self):
        for ex in m.EXCH:
            self.turnover(ex, {})
        self.turnover('binance-um', {'XAUUSDT': [2e6] * 3})
        rows = [lrow('binance-um', 'XAUUSDT', 'commodity'), lrow('binance-um', 'BTCDOMUSDT', 'index', 'crypto-index: не TradFi, в вселенную не берём'),
                lrow('binance-um', 'SPCXUSD1', flag='котировка USD1, не USDT'), lrow('binance-um', 'NEWUSDT')]
        out = self.run_all(rows, [])
        self.assertEqual({r['symbol'] for r in self.read(out, 'sources-binance-um.csv')}, {'XAUUSDT'})
        nt = self.read(out, 'no-turnover.csv')
        self.assertEqual([r['symbol'] for r in nt], ['NEWUSDT'])

    def test_delta_extends_short_turnover(self):
        # оборот TSLA в таблице кончается 3-го дня, у остальных биржа дальше (5 дней): добираем дельту с 4-го дня; NEW — без строк, целиком
        for ex in m.EXCH:
            self.turnover(ex, {})
        self.turnover('binance-um', {'TSLAUSDT': [3e6] * 3, 'XAUUSDT': [3e6] * 5})
        calls = []

        def fake(ex, sym, since=None):
            calls.append((sym, since))
            return [(D0 + timedelta(i), 3e6) for i in range(5) if since is None or D0 + timedelta(i) >= since]
        m.fetch_days = fake
        out = self.run_all([lrow('binance-um', 'TSLAUSDT'), lrow('binance-um', 'XAUUSDT'), lrow('binance-um', 'NEWUSDT')], [], fetch=True)
        self.assertEqual(sorted(calls, key=str), [('NEWUSDT', None), ('TSLAUSDT', D0 + timedelta(3))])
        uni = {r['symbol']: (r['from_day'], r['to_day']) for r in self.read(out, 'universe-binance-um.csv')}
        self.assertEqual(uni['TSLAUSDT'], ('2026-09-02', '2026-09-05'))
        self.assertEqual(uni['NEWUSDT'], ('2026-09-02', '2026-09-05'))
        self.assertEqual({r['symbol'] for r in self.read(out, 'turnover-delta.csv')}, {'TSLAUSDT', 'NEWUSDT'})

    def test_need_candles(self):
        ivs = [(date(2026, 9, 2), date(2026, 9, 20))]
        last = date(2026, 10, 31)
        full = (datetime(2026, 8, 1), datetime(2026, 10, 31, 23, 59))
        self.assertEqual(m.need_candles(ivs, full, last), '')
        self.assertIn('нет файла', m.need_candles(ivs, None, last))
        self.assertIn('до первой 2026-09-02…2026-09-04', m.need_candles(ivs, (datetime(2026, 9, 5), datetime(2026, 10, 31, 23, 59)), last))
        self.assertIn('после последней 2026-09-10…2026-09-20', m.need_candles(ivs, (datetime(2026, 8, 1), datetime(2026, 9, 10, 23, 59)), last))
        # сутки позже последней минуты архива не требуются
        self.assertEqual(m.need_candles([(date(2026, 11, 1), date(2026, 11, 3))], None, last), '')


if __name__ == '__main__':
    unittest.main()
