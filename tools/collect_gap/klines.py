#!/usr/bin/env python3
"""REST-минутки (объём по минуте) по символам пула биржи — эталон «рынок торговал» для сверки покрытия коллектора.

Binance fapi / Bybit v5 linear / OKX history-candles / Gate futures usdt / BingX swap v3. Одна нить, пауза между запросами —
по лимиту биржи. Выход: <out>/<биржа>-klines.npz — syms, minute0, vol float64[nsym,nmin] (объём в единицах биржи, -1 = нет
свечи / нет ответа), ntr int32 (число сделок, только Binance), qv float32 (оборот в USDT; BingX — объём × close), ok bool[nsym] (символ получен без ошибок).
Запуск: python3 klines.py --exchange okx --pools '/data/collect/okx/data/pool-*.json' --out /data/collect-gap/klines --t-end <epoch s>
"""
import argparse, glob, json, os, sys, time, urllib.parse, urllib.request, urllib.error
import numpy as np

T0 = 1794528000
UA = {'User-Agent': 'alpha-collect-gap/1'}


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                wait = int(e.headers.get('Retry-After', '30')) + 5
                print(f'  HTTP {e.code}, sleep {wait}s', flush=True)
                time.sleep(wait)
            elif e.code == 400:
                return {'_err': 400}
            else:
                time.sleep(2 + 3 * i)
        except Exception as e:  # noqa
            time.sleep(2 + 3 * i)
    return None


def fetch_binance(sym, s_ms, e_ms):
    sym = urllib.parse.quote(sym)
    out = {}
    cur = s_ms
    while cur < e_ms:
        d = get(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={cur}&endTime={e_ms}&limit=1500')
        if d is None or isinstance(d, dict):
            return out, False
        if not d:
            break
        for r in d:
            out[r[0] // 60000] = (float(r[5]), int(r[8]), float(r[7]))
        cur = d[-1][0] + 60000
        time.sleep(0.35)
    return out, True


def fetch_bybit(sym, s_ms, e_ms):
    sym = urllib.parse.quote(sym)
    out = {}
    end = e_ms
    while end >= s_ms:
        d = get(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={s_ms}&end={end}&limit=1000')
        if d is None or d.get('retCode') != 0:
            return out, False
        lst = d['result']['list']
        if not lst:
            break
        for r in lst:
            out[int(r[0]) // 60000] = (float(r[5]), 0, float(r[6]))
        end = int(lst[-1][0]) - 60000  # список по убыванию
        time.sleep(0.12)
    return out, True


def fetch_okx(sym, s_ms, e_ms):
    sym = urllib.parse.quote(sym)
    out = {}
    after = e_ms + 60000  # свечи строго старше after
    while True:
        d = get(f'https://www.okx.com/api/v5/market/history-candles?instId={sym}&bar=1m&after={after}&limit=100')
        if d is None or d.get('code') != '0':
            return out, False
        lst = d['data']
        if not lst:
            break
        for r in lst:
            out[int(r[0]) // 60000] = (float(r[5]), 0, float(r[7]))  # vol — в контрактах, r[7] — в USDT
        oldest = int(lst[-1][0])
        if oldest <= s_ms:
            break
        after = oldest
        time.sleep(0.11)
    return out, True


def fetch_gate(sym, s_ms, e_ms):
    sym = urllib.parse.quote(sym)
    out = {}
    cur = s_ms // 1000
    end = e_ms // 1000
    while cur < end:
        to = min(end, cur + 1999 * 60)
        d = get(f'https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={sym}&interval=1m&from={cur}&to={to}')
        if d is None or isinstance(d, dict):
            return out, False
        for r in d:
            out[int(r['t']) // 60] = (float(r.get('v', 0)), 0, float(r.get('sum', 0)))
        cur = to + 60
        time.sleep(0.1)
    return out, True


def fetch_bingx(sym, s_ms, e_ms):
    sym = urllib.parse.quote(sym)
    out = {}
    end = e_ms
    while end >= s_ms:
        for _ in range(4):
            d = get(f'https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol={sym}&interval=1m&startTime={s_ms}&endTime={end}&limit=1440')
            if d is not None and d.get('code') == 0:
                break
            print(f'  bingx {sym}: {str(d)[:200]}', flush=True)
            time.sleep(6)
        if d is None or d.get('code') != 0:
            return out, False
        lst = d.get('data') or []
        if not lst:
            break
        for r in lst:
            out[int(r['time']) // 60000] = (float(r['volume']), 0, float(r['volume']) * float(r['close']))
        oldest = min(int(r['time']) for r in lst)  # ответ — с конца окна назад
        if oldest <= s_ms:
            break
        end = oldest - 60000
        time.sleep(0.6)
    return out, True


FETCH = {'binance': fetch_binance, 'bybit': fetch_bybit, 'okx': fetch_okx, 'gate': fetch_gate, 'bingx': fetch_bingx}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', required=True)
    ap.add_argument('--pools', default='', help='glob pool-*.json')
    ap.add_argument('--extra-syms', default='', help='файл со списком символов (по строке), добавить к пулам')
    ap.add_argument('--out', required=True)
    ap.add_argument('--t-end', type=int, required=True)
    ap.add_argument('--t-start', type=int, default=T0)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--pname', default='', help='имя части в файле (по умолчанию p<i>); для добора — например x')
    ap.add_argument('--part', default='0/1', help='i/n — часть списка символов (параллельные процессы)')
    a = ap.parse_args()
    syms = set()
    for p in (glob.glob(a.pools) if a.pools else []):
        syms |= set(json.load(open(p))['pool'])
    if a.extra_syms:
        syms |= {l.strip() for l in open(a.extra_syms) if l.strip()}
    syms = sorted(syms)
    if a.limit:
        syms = syms[:a.limit]
    pi, pn = map(int, a.part.split('/'))
    syms = syms[pi::pn]
    minute0 = a.t_start // 60
    nmin = a.t_end // 60 - minute0
    vol = np.full((len(syms), nmin), -1.0)
    ntr = np.full((len(syms), nmin), -1, dtype=np.int32)
    qv = np.full((len(syms), nmin), -1.0, dtype=np.float32)
    ok = np.zeros(len(syms), dtype=bool)
    fetch = FETCH[a.exchange]
    t00 = time.time()
    for i, s in enumerate(syms):
        got, good = fetch(s, a.t_start * 1000, a.t_end * 1000 - 1)
        ok[i] = good
        for m, (v, n, q) in got.items():
            j = m - minute0
            if 0 <= j < nmin:
                vol[i, j] = v
                ntr[i, j] = n
                qv[i, j] = q
        if i % 25 == 0:
            print(f'  {i + 1}/{len(syms)} {s} candles={len(got)} ok={good} t={time.time() - t00:.0f}s', flush=True)
    os.makedirs(a.out, exist_ok=True)
    np.savez_compressed(os.path.join(a.out, a.exchange + f'-klines-p{a.pname or pi}.npz'), syms=np.array(syms), minute0=np.array([minute0]), vol=vol, ntr=ntr, qv=qv, ok=ok)
    print(f'done {a.exchange}: {len(syms)} syms, ok={int(ok.sum())}, {time.time() - t00:.0f}s', flush=True)


if __name__ == '__main__':
    main()
