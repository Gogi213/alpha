"""TK-488: итоговая таблица класса (tk488_report.py), офлайн: причина дыр только из чисел coverage и need-candles; Gate-фандинг читается как у остальных бирж;
контракт без файла свечей / с дырой / с требованием вселенной до первой минуты файла / без файла фандинга."""
import csv, gzip, importlib.util, os, shutil, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488r', os.path.join(HERE, '..', 'tk488_report.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

LIST_HDR = ['exchange', 'symbol', 'class', 'class_src', 'subtype', 'contract_type', 'status', 'onboard_ms', 'meta_source', 'flag']
COV_HDR = ['биржа', 'символ', 'первая_utc', 'последняя_utc', 'строк', 'ожидаемо_минут', 'пропущено_минут', 'дыр', 'макс_дыра_мин', 'дублей', 'не_по_порядку', 'не_на_минуте', 'ошибка']


def cov(ex, sym, a, b, rows, gaps=0, miss=0, mx=0):
    return [ex, sym, a, b, rows, rows + miss, miss, gaps, mx, 0, 0, 0, '']


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, name, hdr, rows):
        p = os.path.join(self.d, name)
        with open(p, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(hdr)
            w.writerows(rows)
        return p

    def run_build(self):
        lst = self.write('list.csv', LIST_HDR, [
            ['binance-um', 'OKUSDT', 'equity', 'X', 'Eq', 'PERPETUAL', 'TRADING', '1', 'meta', ''],
            ['binance-um', 'NOFILEUSDT', 'etf', 'X', '', 'PERPETUAL', 'TRADING', '1', 'meta', ''],
            ['bybit-linear', 'GAPUSDT', 'equity', 'X', '', 'LinearPerpetual', 'Trading', '1', 'meta', ''],
            ['bybit-linear', 'EARLYUSDT', 'commodity', 'X', '', 'LinearPerpetual', 'Trading', '1', 'meta', ''],
            ['gate-futures_usdt', 'AAPL_USDT', 'equity', 'X', '', 'direct', 'trading', '1', 'meta', 'pre-market'],
        ])
        uni = os.path.join(self.d, 'uni')
        os.makedirs(uni)
        for ex, rows in {'binance-um': [['OKUSDT', '2026-09-02', '2026-09-30'], ['NOFILEUSDT', '2026-09-02', '2026-09-30']],
                         'bybit-linear': [['GAPUSDT', '2026-09-02', '2026-09-30'], ['EARLYUSDT', '2026-08-01', '2026-09-30']],
                         'gate-futures_usdt': [['AAPL_USDT', '2026-09-02', '2026-09-03']]}.items():
            self.write(f'uni/universe-{ex}.csv', ['symbol', 'from_day', 'to_day'], rows)
        covf = self.write('cov.csv', COV_HDR, [
            cov('binance-um', 'OKUSDT', '2026-09-01 00:00', '2026-09-30 23:59', 43200),
            cov('bybit-linear', 'GAPUSDT', '2026-09-01 00:00', '2026-09-30 23:59', 43100, 1, 100, 100),
            cov('bybit-linear', 'EARLYUSDT', '2026-09-10 00:00', '2026-09-30 23:59', 1000),
            cov('gate-futures_usdt', 'AAPL_USDT', '2026-09-01 00:00', '2026-09-30 23:59', 43200),
        ])
        fd = os.path.join(self.d, 'fund')
        os.makedirs(os.path.join(fd, 'binance-um'))
        with gzip.open(os.path.join(fd, 'binance-um', 'OKUSDT.csv.gz'), 'wt', newline='') as f:
            f.write('time_ms,rate,interval_h\n1788220800000,0.0001,8\n1788249600000,0.0001,8\n')
        os.makedirs(os.path.join(fd, 'gate-futures_usdt'))
        with gzip.open(os.path.join(fd, 'gate-futures_usdt', 'AAPL_USDT.csv.gz'), 'wt', newline='') as f:
            f.write('time_ms,rate,interval_h\n1788220800000,0,8\n')
        out = m.build(lst, uni, covf, fd)
        return {(r[0], r[1]): dict(zip(m.COLS, r)) for r in out}

    def test_reasons_and_funding(self):
        r = self.run_build()
        ok = r[('binance-um', 'OKUSDT')]
        self.assertEqual((ok['причина'], ok['фандинг_записей'], ok['дней_во_вселенной']), ('ок', 2, 29))
        nf = r[('binance-um', 'NOFILEUSDT')]
        self.assertEqual(nf['причина'], 'нет файла свечей')
        self.assertEqual(nf['фандинг_записей'], 'нет файла')
        gap = r[('bybit-linear', 'GAPUSDT')]
        self.assertEqual(gap['причина'], 'дыр 1, пропущено 100 мин (макс. 100)')
        early = r[('bybit-linear', 'EARLYUSDT')]
        self.assertIn('до первой 2026-08-01…2026-09-09', early['причина'])
        self.assertEqual(early['не_хватает'], 'до первой 2026-08-01…2026-09-09')
        g = r[('gate-futures_usdt', 'AAPL_USDT')]
        self.assertEqual((g['причина'], g['фандинг_записей'], g['флаг']), ('ок', 1, 'pre-market'))  # Gate-фандинг читается из каталога, не «вне TK-478»

    def test_listing_violations(self):
        # Н7: вселенная не раньше листинга -1 сут; нарушение (символ занят чужим контрактом) попадает в список
        listing = 1788220800000  # 2026-09-01 UTC
        lst = self.write('l2.csv', LIST_HDR, [['binance-um', 'OKUSDT', 'equity', 'X', '', 'P', 'TRADING', str(listing), 'm', ''],
                                              ['binance-um', 'REUSEDUSDT', 'equity', 'X', '', 'P', 'TRADING', str(listing), 'm', ''],
                                              ['binance-um', 'NODATEUSDT', 'equity', 'X', '', 'P', 'TRADING', '', 'm', '']])
        uni = os.path.join(self.d, 'u2')
        os.makedirs(uni)
        for ex in m.u.EXCH:
            self.write(f'u2/universe-{ex}.csv', ['symbol', 'from_day', 'to_day'],
                       [['OKUSDT', '2026-08-31', '2026-09-30'], ['REUSEDUSDT', '2026-08-01', '2026-09-30'], ['NODATEUSDT', '2020-01-01', '2026-09-30']] if ex == 'binance-um' else [])
        v = m.listing_violations(m.u.tradfi_rows(lst), uni)
        self.assertEqual([(x[0], x[1]) for x in v], [('binance-um', 'REUSEDUSDT')])

    def test_no_funding_dir(self):
        lst = self.write('l.csv', LIST_HDR, [['binance-um', 'XUSDT', 'fx', 'X', '', 'P', 'TRADING', '1', 'meta', '']])
        uni = os.path.join(self.d, 'u2')
        os.makedirs(uni)
        for ex in m.u.EXCH:
            self.write(f'u2/universe-{ex}.csv', ['symbol', 'from_day', 'to_day'], [['XUSDT', '2026-09-02', '2026-09-03']] if ex == 'binance-um' else [])
        covf = self.write('c.csv', COV_HDR, [cov('binance-um', 'XUSDT', '2026-09-01 00:00', '2026-09-30 23:59', 10)])
        row = m.build(lst, uni, covf, '-')[0]
        self.assertEqual(row[m.COLS.index('фандинг_записей')], 'не считали')


if __name__ == '__main__':
    unittest.main()
