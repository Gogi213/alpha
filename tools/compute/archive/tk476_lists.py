#!/usr/bin/env python3
"""TK-476 (В-60): списки монет по дневному обороту до скачивания свечей.
Оборот суток = quote turnover (USDT) по дневным свечам REST биржи; монета проходит, если медиана оборота последних 30
ПОЛНЫХ суток (UTC, текущая неполная отброшена) >= порога. Binance USDT-M perpetual (PERPETUAL, TRADING) >= 5 млн,
Bybit linear USDT perpetual (Trading) >= 1 млн, Gate futures USDT (trading) >= 1 млн.
Использование: tk476_lists.py <выход.csv> [--have binance-um=файл bybit-linear=файл gate-futures_usdt=файл] — файл: имена *.csv.gz построчно.
Выход: exchange,symbol,base,start_ms,days,median30_usdt,min_usdt,passes,have_file."""
import csv, json, statistics, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

TH = {'binance-um': 5e6, 'bybit-linear': 1e6, 'gate-futures_usdt': 1e6}
DAY = 86400


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'alpha-tk476'}), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def today_start():
    return int(time.time()) // DAY * DAY


def binance():
    info = get('https://fapi.binance.com/fapi/v1/exchangeInfo')['symbols']
    syms = [s for s in info if s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING']

    def one(s):
        k = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={urllib.parse.quote(s['symbol'])}&interval=1d&limit=31")
        k = [x for x in k if x[0] // 1000 < today_start()][-30:]
        return s['symbol'], s['baseAsset'], s.get('onboardDate', ''), [float(x[7]) for x in k]
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, syms))


def bybit():
    out, cur = [], ''
    while True:
        r = get('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000' + (f'&cursor={cur}' if cur else ''))['result']
        out += r['list']
        cur = r.get('nextPageCursor')
        if not cur:
            break
    syms = [s for s in out if s['contractType'] == 'LinearPerpetual' and s['quoteCoin'] == 'USDT' and s['status'] == 'Trading']

    def one(s):
        k = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={urllib.parse.quote(s['symbol'])}&interval=D&limit=31")['result']['list']
        k = sorted([x for x in k if int(x[0]) // 1000 < today_start()], key=lambda x: int(x[0]))[-30:]
        return s['symbol'], s['baseCoin'], s.get('launchTime', ''), [float(x[6]) for x in k]
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(one, syms))


def gate():
    info = [c for c in get('https://api.gateio.ws/api/v4/futures/usdt/contracts') if c.get('status') == 'trading' and not c.get('is_pre_market')]

    def one(c):
        k = get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={urllib.parse.quote(c['name'])}&interval=1d&limit=31")
        k = [x for x in k if x['t'] < today_start()][-30:]
        return c['name'], c['name'].split('_')[0], str(c.get('launch_time', c.get('create_time', 0)) * 1000), [float(x['sum']) for x in k]
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, info))


def main():
    out = sys.argv[1]
    have = {}
    for a in sys.argv[2:]:
        if '=' in a:
            ex, fn = a.split('=', 1)
            have[ex] = {l.strip().removesuffix('.csv.gz') for l in open(fn) if l.strip().endswith('.csv.gz')}
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'base', 'start_ms', 'days', 'median30_usdt', 'min30_usdt', 'passes', 'have_file'])
        for ex, fn in (('binance-um', binance), ('bybit-linear', bybit), ('gate-futures_usdt', gate)):
            n = 0
            for sym, base, start, v in fn():
                med = statistics.median(v) if v else 0.0
                p = int(med >= TH[ex])
                n += p
                w.writerow([ex, sym, base, start, len(v), f'{med:.0f}', f'{min(v):.0f}' if v else 0, p, int(sym in have.get(ex, ()))])
            print(ex, 'passes', n, flush=True)


if __name__ == '__main__':
    main()
