"""TK-488: классификация TradFi по метаданным бирж (tk488_tradfi_list.py), офлайн — ответы бирж подменены.
Граничные входы: крипто-индекс Binance (INDEX+Crypto), pre-market без подтипа TradFi, котировка не USDT, Gate pre-market, пустой
подтип/регион Bybit, снятые контракты, неизвестный тип."""
import importlib.util, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488', os.path.join(HERE, '..', 'tk488_tradfi_list.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BN = {'symbols': [
    {'symbol': 'TSLAUSDT', 'underlyingType': 'EQUITY', 'underlyingSubType': ['TradFi'], 'contractType': 'TRADIFI_PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT', 'onboardDate': 1},
    {'symbol': 'BTCDOMUSDT', 'underlyingType': 'INDEX', 'underlyingSubType': ['Index', 'Crypto'], 'contractType': 'PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
    {'symbol': 'PREDICTUSDT', 'underlyingType': 'PREMARKET', 'underlyingSubType': ['Pre-Market'], 'contractType': 'PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
    {'symbol': 'OPENAIUSDT', 'underlyingType': 'PREMARKET', 'underlyingSubType': ['Pre-IPO', 'TradFi'], 'contractType': 'TRADIFI_PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
    {'symbol': 'XAUUSDT', 'underlyingType': 'COMMODITY', 'underlyingSubType': ['TradFi'], 'contractType': 'TRADIFI_PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
    {'symbol': 'SPYUSD1', 'underlyingType': 'EQUITY', 'underlyingSubType': ['TradFi'], 'contractType': 'TRADIFI_PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USD1'},
    {'symbol': 'BTCUSDT', 'underlyingType': 'COIN', 'underlyingSubType': ['PoW'], 'contractType': 'PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
    {'symbol': 'NOTYPE', 'contractType': 'PERPETUAL', 'status': 'TRADING', 'quoteAsset': 'USDT'},
]}
BY = [
    {'symbol': 'AAPLUSDT', 'symbolType': 'stock', 'marketRegion': 'US', 'contractType': 'LinearPerpetual', 'status': 'Trading', 'quoteCoin': 'USDT', 'launchTime': '1'},
    {'symbol': 'EWYUSDT', 'symbolType': 'ETF', 'marketRegion': 'KR', 'contractType': 'LinearPerpetual', 'status': 'Trading', 'quoteCoin': 'USDT'},
    {'symbol': 'XAUUSDT', 'symbolType': 'commodity', 'marketRegion': '', 'contractType': 'LinearPerpetual', 'status': 'Closed', 'quoteCoin': 'USDT'},
    {'symbol': 'FOO', 'symbolType': 'innovation', 'marketRegion': '', 'contractType': 'LinearPerpetual', 'status': 'Trading', 'quoteCoin': 'USDT'},
    {'symbol': 'BTCUSDT', 'symbolType': '', 'marketRegion': '', 'contractType': 'LinearPerpetual', 'status': 'Trading', 'quoteCoin': 'USDT'},
]
GT = [
    {'name': 'AAPL_USDT', 'contract_type': 'stocks', 'type': 'direct', 'status': 'trading', 'is_pre_market': False, 'launch_time': 5},
    {'name': 'ANTHROPIC_USDT', 'contract_type': 'stocks', 'type': 'direct', 'status': 'trading', 'is_pre_market': True, 'create_time': 7},
    {'name': 'XAU_USDT', 'contract_type': 'metals', 'type': 'direct', 'status': 'trading', 'is_pre_market': False},
    {'name': 'BTC_USDT', 'contract_type': '', 'type': 'direct', 'status': 'trading', 'is_pre_market': False},
]


class Classify(unittest.TestCase):
    def run_all(self):
        def fake(url, tries=6):
            if 'exchangeInfo' in url:
                return BN
            if 'instruments-info' in url:
                st = url.split('status=')[1].split('&')[0]
                return {'result': {'list': [s for s in BY if s['status'] == st], 'nextPageCursor': ''}}
            return GT
        m.get = fake
        out = []
        for fn in (m.binance, m.bybit, m.gate):
            out += list(fn(lambda *a: None))
        return {(r[0], r[1]): r for r in out}

    def test_binance(self):
        r = self.run_all()
        self.assertEqual(r[('binance-um', 'TSLAUSDT')][2], 'equity')
        self.assertEqual(r[('binance-um', 'XAUUSDT')][2], 'commodity')
        self.assertIn('crypto-index', r[('binance-um', 'BTCDOMUSDT')][9])
        self.assertIn('не TradFi', r[('binance-um', 'PREDICTUSDT')][9])
        self.assertIn('Судье', r[('binance-um', 'OPENAIUSDT')][9])
        self.assertIn('USD1', r[('binance-um', 'SPYUSD1')][9])
        self.assertNotIn(('binance-um', 'BTCUSDT'), r)
        self.assertNotIn(('binance-um', 'NOTYPE'), r)  # нет underlyingType — не TradFi, без падения

    def test_bybit(self):
        r = self.run_all()
        self.assertEqual(r[('bybit-linear', 'AAPLUSDT')][2], 'equity')
        self.assertEqual(r[('bybit-linear', 'EWYUSDT')][2], 'etf-kr')
        self.assertEqual(r[('bybit-linear', 'XAUUSDT')][2:7:4], ['commodity', 'Closed'])  # снятый тоже в списке
        self.assertNotIn(('bybit-linear', 'FOO'), r)  # innovation — крипто
        self.assertNotIn(('bybit-linear', 'BTCUSDT'), r)

    def test_gate(self):
        r = self.run_all()
        self.assertEqual(r[('gate-futures_usdt', 'AAPL_USDT')][7], '5000')
        self.assertEqual(r[('gate-futures_usdt', 'XAU_USDT')][2], 'metal')
        self.assertIn('Судье', r[('gate-futures_usdt', 'ANTHROPIC_USDT')][9])
        self.assertEqual(r[('gate-futures_usdt', 'ANTHROPIC_USDT')][7], '7000')  # launch_time нет → create_time
        self.assertNotIn(('gate-futures_usdt', 'BTC_USDT'), r)


if __name__ == '__main__':
    unittest.main()
