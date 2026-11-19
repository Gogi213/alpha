#!/usr/bin/env python3
"""Независимый состав «выше порога» по REST биржи: торгуемые бессрочные USDT-контракты с оборотом за сутки ≥ порога.

Свои запросы к публичному REST (те же конечные точки, что у адаптеров, но разбор здесь, не в коллекторе) — эталон для сверки
«символы с данными» против того, что биржа считает выше порога. Имена символов — как в файлах коллектора (Binance/Bybit `BTCUSDT`,
OKX `BTC-USDT-SWAP`, Gate `BTC_USDT`, BingX `BTC-USDT`).
Выход: JSON {"ts": epoch, "min_qv": порог, "qv": {символ: оборот за сутки, $}} — символы с оборотом ≥ порог × --floor (0.5 по умолчанию, запас на границе).
Запуск: python3 univ.py --exchange bybit --out /data/perf/poolfix-deploy/<метка>/bybit/univ.json
"""
import argparse, json, time, urllib.request, urllib.parse

UA = {'User-Agent': 'alpha-poolfix-univ/1'}


def get(url):
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa
            last = e
            time.sleep(2 + 3 * i)
    raise last


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def binance():
    info = get('https://fapi.binance.com/fapi/v1/exchangeInfo')
    live = {s['symbol'] for s in info['symbols'] if s.get('status') == 'TRADING' and s.get('contractType') == 'PERPETUAL' and s.get('quoteAsset') == 'USDT'}
    return {t['symbol']: num(t.get('quoteVolume')) for t in get('https://fapi.binance.com/fapi/v1/ticker/24hr') if t['symbol'] in live}


def bybit():
    live, cur = set(), ''
    while True:
        d = get('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000' + (f'&cursor={urllib.parse.quote(cur)}' if cur else ''))['result']
        live |= {s['symbol'] for s in d['list'] if s.get('status') == 'Trading' and s.get('contractType') == 'LinearPerpetual' and s.get('quoteCoin') == 'USDT'}
        cur = d.get('nextPageCursor') or ''
        if not cur:
            break
    return {t['symbol']: num(t.get('turnover24h')) for t in get('https://api.bybit.com/v5/market/tickers?category=linear')['result']['list'] if t['symbol'] in live}


def okx():
    ins = get('https://www.okx.com/api/v5/public/instruments?instType=SWAP')['data']
    live = {s['instId'] for s in ins if s.get('ctType') == 'linear' and s.get('settleCcy') == 'USDT' and s.get('state') == 'live'}
    out = {}
    for t in get('https://www.okx.com/api/v5/market/tickers?instType=SWAP')['data']:
        v, last = num(t.get('volCcy24h')), num(t.get('last'))
        if t['instId'] in live and v is not None and last is not None:
            out[t['instId']] = v * last  # оборот в $ — приближение: база × последняя цена (в тикере OKX его нет)
    return out


def gate():
    live = {c['name'] for c in get('https://api.gateio.ws/api/v4/futures/usdt/contracts') if c.get('status') == 'trading'}
    return {t['contract']: num(t.get('volume_24h_quote')) for t in get('https://api.gateio.ws/api/v4/futures/usdt/tickers') if t['contract'] in live}


def bingx():
    cs = get('https://open-api.bingx.com/openApi/swap/v2/quote/contracts')['data']
    live = {c['symbol'] for c in cs if c.get('currency') == 'USDT' and c.get('status') == 1}
    return {t['symbol']: num(t.get('quoteVolume')) for t in get('https://open-api.bingx.com/openApi/swap/v2/quote/ticker')['data'] if t['symbol'] in live}


F = {'binance': binance, 'bybit': bybit, 'okx': okx, 'gate': gate, 'bingx': bingx}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--min-qv', type=float, default=300000.0)
    ap.add_argument('--floor', type=float, default=0.5)
    a = ap.parse_args()
    qv = {s: v for s, v in F[a.exchange]().items() if v is not None and v >= a.min_qv * a.floor}
    json.dump({'ts': int(time.time()), 'min_qv': a.min_qv, 'floor': a.floor, 'qv': qv}, open(a.out, 'w'))
    print(f'{a.exchange}: {len(qv)} contracts >= {a.min_qv * a.floor:.0f}, of them >= {a.min_qv:.0f}: {sum(1 for v in qv.values() if v >= a.min_qv)}')


if __name__ == '__main__':
    main()
