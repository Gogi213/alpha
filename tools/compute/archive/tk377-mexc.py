#!/usr/bin/env python3
"""TK-377 п.3-4: монеты Gate-фьюч без Bybit: покрытие DEX (ETH/Base USD-слой) и оборот; MEXC фьюч: листинг, комиссии, лимиты, свечи 1м за 15-21.10.
Запуск: tk377-mexc.py OUT_CSV  (нужен список DEX-монет dex.txt рядом с universe: аргумент 2)"""
import sys, csv, json, urllib.request, collections
UNIV = 'docs/findings/gate-week-universe-2026-10-23.csv'
dex = set(open(sys.argv[2]).read().split()) if len(sys.argv) > 2 else set()
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=30))
det = {d['symbol']: d for d in get('https://contract.mexc.com/api/v1/contract/detail')['data']}
rows = []
for r in csv.DictReader(open(UNIV, encoding='utf-8-sig')):
    if r['market'] != 'фьюч': continue
    c = r['symbol'][:-5]; m = det.get(r['symbol'])
    row = dict(coin=c, gate_turn_usd=float(r['turnover24h_usd']), dex_ethbase=int(c in dex), mexc_listed=int(m is not None))
    if m:
        row.update(mexc_taker=m['takerFeeRate'], mexc_maker=m['makerFeeRate'], mexc_maxVol=m['maxVol'], mexc_contractSize=m['contractSize'], mexc_state=m['state'], mexc_maxLev=m['maxLeverage'])
        k = get(f"https://contract.mexc.com/api/v1/contract/kline/{r['symbol']}?interval=Min1&start=1792022400&end=1792627200")
        t = k['data']['time']; row.update(mexc_kline_n=len(t), mexc_kline_first=t[0] if t else '', mexc_kline_vol_usd=round(sum(v*q*m['contractSize'] for v, q in zip(k['data']['close'], k['data']['vol'])) , 0) if t else 0)
    rows.append(row)
cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ('coin', 'gate_turn_usd', 'dex_ethbase', 'mexc_listed'), k))
w = csv.DictWriter(open(sys.argv[1], 'w', newline='', encoding='utf-8'), cols); w.writeheader(); w.writerows(rows)
tot = sum(r['gate_turn_usd'] for r in rows)
print('fut coins', len(rows), 'turn', round(tot))
print('dex eth/base', sum(r['dex_ethbase'] for r in rows), 'turn', round(sum(r['gate_turn_usd'] for r in rows if r['dex_ethbase'])))
print('mexc listed', sum(r['mexc_listed'] for r in rows), 'turn', round(sum(r['gate_turn_usd'] for r in rows if r['mexc_listed'])))
print('mexc fee taker', collections.Counter(r.get('mexc_taker') for r in rows if r['mexc_listed']), 'maker', collections.Counter(r.get('mexc_maker') for r in rows if r['mexc_listed']))
print('state', collections.Counter(r.get('mexc_state') for r in rows if r['mexc_listed']))
