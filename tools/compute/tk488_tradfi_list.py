#!/usr/bin/env python3
"""TK-488 (В-93): список ВСЕХ TradFi-контрактов Binance USD-M / Bybit linear / Gate futures USDT по метаданным биржи,
не по списку имён. Binance — exchangeInfo (underlyingType/underlyingSubType/contractType), Bybit — instruments-info
Trading+Closed+Delivering (symbolType/marketRegion), Gate — futures/usdt/contracts (contract_type, is_pre_market).
Снятые контракты Binance/Gate в живых метаданных отсутствуют (Binance — только SETTLING) — их ловит этап оборота по архивам
(tk476_universe.py), здесь они не классифицируются и идут Судье списком в отчёте.
usage: tk488_tradfi_list.py <out.csv> [--json-dir <каталог>]   (с --json-dir сохраняет сырые ответы бирж рядом)
Выход: exchange,symbol,class,class_src,subtype,contract_type,status,onboard_ms,meta_source,flag."""
import csv, json, os, sys, time, urllib.parse, urllib.request

UA = {'User-Agent': 'alpha-tk488'}


def get(url, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


# Binance: underlyingType → класс. INDEX без подтипа TradFi (BTCDOM/DEFI/ALL: подтип Index или Index|Crypto) — крипто-индекс, не TradFi (flag=crypto-index).
BN_CLASS = {'EQUITY': 'equity', 'HK_EQUITY': 'equity-hk', 'KR_EQUITY': 'equity-kr', 'CN_EQUITY': 'equity-cn',
            'COMMODITY': 'commodity', 'FX': 'fx', 'PREMARKET': 'pre-ipo', 'INDEX': 'index'}
# Bybit: symbolType (+ marketRegion) → класс.
BY_CLASS = {'stock': 'equity', 'ETF': 'etf', 'commodity': 'commodity', 'forex': 'fx'}
# Gate: contract_type → класс.
GT_CLASS = {'stocks': 'equity', 'indices': 'index', 'metals': 'metal', 'forex': 'fx', 'commodities': 'commodity'}
# Базовый актив fx-контракта — стейбл-коин (USDC/USDT: крипто-пара, не фиат) — не TradFi; токены на золото остаются с пометкой.
STABLE = {'USDC', 'USDT', 'USDE', 'DAI', 'FDUSD', 'TUSD', 'USD1', 'PYUSD', 'BUSD'}
GOLD_TOKENS = {'PAXG', 'XAUT'}
FLAG_STABLE = 'стейбл-коин: не TradFi, в вселенную не берём'
FLAG_GOLD = 'токен на золото (крипто-актив), оставлен во вселенной'


def binance(dump):
    info = get('https://fapi.binance.com/fapi/v1/exchangeInfo')
    dump('binance-exchangeInfo.json', info)
    for s in info['symbols']:
        ut, sub = s.get('underlyingType'), s.get('underlyingSubType', [])
        if ut not in BN_CLASS:
            continue
        flag = ''
        if ut == 'INDEX' and 'TradFi' not in sub:
            flag = 'crypto-index: не TradFi, в вселенную не берём'
        elif ut == 'PREMARKET':
            flag = 'pre-IPO/pre-market: TradFi по подтипу, сомнительно — Судье' if 'TradFi' in sub else 'pre-market крипто: не TradFi'
        elif ut == 'FX' and s.get('baseAsset') in STABLE:
            flag = FLAG_STABLE
        elif s['quoteAsset'] != 'USDT':
            flag = f"котировка {s['quoteAsset']}, не USDT"
        yield ['binance-um', s['symbol'], BN_CLASS[ut], ut, '|'.join(sub), s['contractType'], s['status'], s.get('onboardDate', ''),
               'exchangeInfo underlyingType/underlyingSubType', flag]


def bybit(dump):
    allr = []
    for st in ('Trading', 'Closed', 'Delivering'):
        cur = ''
        while True:
            r = get('https://api.bybit.com/v5/market/instruments-info?category=linear&status=%s&limit=1000' % st
                    + (f'&cursor={urllib.parse.quote(cur)}' if cur else ''))['result']
            allr += r['list']
            cur = r.get('nextPageCursor')
            if not cur:
                break
    dump('bybit-instruments-info.json', allr)
    for s in allr:
        t = s.get('symbolType', '')
        if t not in BY_CLASS:
            continue
        cls = BY_CLASS[t]
        region = s.get('marketRegion', '')
        flag = FLAG_STABLE if cls == 'fx' and s.get('baseCoin') in STABLE else '' if s['quoteCoin'] == 'USDT' else f"котировка {s['quoteCoin']}, не USDT"
        yield ['bybit-linear', s['symbol'], cls if not region or region == 'US' else f'{cls}-{region.lower()}', t, region, s['contractType'],
               s['status'], s.get('launchTime', ''), 'instruments-info symbolType/marketRegion', flag]


def gate(dump):
    info = get('https://api.gateio.ws/api/v4/futures/usdt/contracts')
    dump('gate-contracts.json', info)
    for c in info:
        t = c.get('contract_type', '')
        if t not in GT_CLASS:
            continue
        base = c['name'].split('_')[0]
        flag = ('pre-market контракт: сомнительно — Судье' if c.get('is_pre_market') else FLAG_STABLE if t == 'forex' and base in STABLE
                else FLAG_GOLD if t == 'metals' and base in GOLD_TOKENS else '')
        yield ['gate-futures_usdt', c['name'], GT_CLASS[t], t, '', c['type'], c['status'], str(c.get('launch_time', c.get('create_time', 0)) * 1000),
               'contracts contract_type', flag]


def main():
    out = sys.argv[1]
    jd = sys.argv[sys.argv.index('--json-dir') + 1] if '--json-dir' in sys.argv else None

    def dump(name, obj):
        if jd:
            os.makedirs(jd, exist_ok=True)
            json.dump(obj, open(os.path.join(jd, name), 'w'))
    rows = []
    for fn in (binance, bybit, gate):
        rows += list(fn(dump))
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'class', 'class_src', 'subtype', 'contract_type', 'status', 'onboard_ms', 'meta_source', 'flag'])
        w.writerows(rows)
    from collections import Counter
    for k, v in sorted(Counter((r[0], r[2], r[6]) for r in rows).items()):
        print(*k, v)


if __name__ == '__main__':
    main()
