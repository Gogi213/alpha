#!/usr/bin/env python3
"""TK-377 шаг 2(б): биржа x класс (без/с Binance|Bybit|OKX) x покрытие DEX-слоя TK-374 (ETH/Base полные, BSC 15-17.10).
Вход: DIR с contracts/tickers бирж (gate, bitget, bingx, lbank, binance, bybit, okx, *_tick.json) и dex_{ethereum,base,bsc}.txt.
Запуск: tk377-universe.py DIR OUT_PREFIX. Спред: 5 снимков тикеров с паузой 20 с (текущий, не 15-21.10)."""
import sys, json, re, csv, time, statistics, urllib.request, collections
D, OUT = sys.argv[1], sys.argv[2]
def load(f):
    return json.load(open(f'{D}/{f}.json'))
def norm(b):
    b = b.upper(); b = re.sub(r'^(1M|1000000|100000|10000|1000|100)(?=[A-Z])', '', b); return re.sub(r'^K(?=[A-Z]{2,})', '', b) if b.startswith('K') and False else b
heavy = {}
for s in load('binance')['symbols']:
    if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING': heavy.setdefault(norm(s['baseAsset']), set()).add('Binance')
for s in load('bybit')['result']['list']:
    if s['quoteCoin'] == 'USDT' and s['contractType'] == 'LinearPerpetual' and s['status'] == 'Trading': heavy.setdefault(norm(s['baseCoin']), set()).add('Bybit')
for s in load('okx')['data']:
    if s['instId'].endswith('-USDT-SWAP') and s['state'] == 'live': heavy.setdefault(norm(s['instId'].split('-')[0]), set()).add('OKX')
dex = {n: {norm(x.strip()) for x in open(f'{D}/dex_{n}.txt', encoding='utf-8') if x.strip()} for n in ('ethereum', 'base', 'bsc')}
def pull(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))
# биржи: contracts -> {base: (symbol, fee_taker, fee_maker, lim)}, tickers URL -> {symbol: (turn_usd, bid, ask)}
EX = {}
g = load('gate'); EX['Gate'] = {'url': 'https://api.gateio.ws/api/v4/futures/usdt/tickers', 'fee': 'contracts.taker_fee_rate / maker_fee_rate',
    'c': {norm(x['name'].split('_')[0]): (x['name'], float(x['taker_fee_rate']), float(x['maker_fee_rate']), float(x['risk_limit_max'])) for x in g if x['name'].endswith('_USDT') and x['status'] == 'trading' and not x['in_delisting']},
    't': lambda d: {x['contract']: (float(x['volume_24h_quote']), float(x['highest_bid'] or 0), float(x['lowest_ask'] or 0)) for x in d}}
b = load('bitget')['data']; EX['Bitget'] = {'url': 'https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES', 'fee': 'contracts.takerFeeRate / makerFeeRate',
    'c': {norm(x['baseCoin']): (x['symbol'], float(x['takerFeeRate']), float(x['makerFeeRate']), float(x['maxOrderQty'] or 0)) for x in b if x['quoteCoin'] == 'USDT' and x['symbolType'] == 'perpetual' and x['symbolStatus'] == 'normal'},
    't': lambda d: {x['symbol']: (float(x['usdtVolume']), float(x['bidPr'] or 0), float(x['askPr'] or 0)) for x in d['data']}}
bx = load('bingx')['data']; EX['BingX'] = {'url': 'https://open-api.bingx.com/openApi/swap/v2/quote/ticker', 'fee': 'contracts.takerFeeRate / makerFeeRate',
    'c': {norm(x['symbol'].split('-')[0]): (x['symbol'], float(x['takerFeeRate']), float(x['makerFeeRate']), float('nan')) for x in bx if x['symbol'].endswith('-USDT') and x['status'] == 1},
    't': lambda d: {x['symbol']: (float(x['quoteVolume']), float(x['bidPrice'] or 0), float(x['askPrice'] or 0)) for x in d['data']}}
lb = load('lbank')['data']; EX['LBank'] = {'url': 'https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketData?productGroup=SwapU', 'fee': 'НЕТ в contracts (instrument)',
    'c': {norm(x['baseCurrency']): (x['symbol'], float('nan'), float('nan'), float(x['maxOrderVolume'])) for x in lb if x['symbol'].endswith('USDT')},
    't': lambda d: {x['symbol']: (float(x['turnover']), 0.0, 0.0) for x in d['data']}}
snaps = collections.defaultdict(list)  # (ex, sym) -> [(turn, rel_spread_bp)]
for i in range(5):
    for ex, e in EX.items():
        try: t = e['t'](pull(e['url']))
        except Exception as ex_: print(ex, 'ERR', ex_); continue
        for s, (tv, bid, ask) in t.items():
            sp = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else float('nan')
            snaps[(ex, s)].append((tv, sp))
    if i < 4: time.sleep(20)
rows, coins = [], []
for ex, e in EX.items():
    for base, (sym, tk, mk, lim) in e['c'].items():
        sn = snaps.get((ex, sym), []);
        if not sn: continue
        turn = statistics.median(x[0] for x in sn); sps = [x[1] for x in sn if x[1] == x[1]]
        spread = statistics.median(sps) if sps else float('nan')
        cls = 'с тяжеловесами' if base in heavy else 'без'
        nets = [n for n in ('ethereum', 'base', 'bsc') if base in dex[n]]
        cov = 'ETH/Base' if any(n in nets for n in ('ethereum', 'base')) else ('BSC(15-17.10)' if nets else 'нет в слое')
        coins.append(dict(exchange=ex, base=base, symbol=sym, cls=cls, heavy=','.join(sorted(heavy.get(base, []))), turn_usd=round(turn), spread_bp_snapshot=round(spread, 1) if spread == spread else '',
                          taker=tk, maker=mk, limit=lim, dex_nets=','.join(nets), dex_cov=cov))
w = csv.DictWriter(open(OUT + '-coins.csv', 'w', newline='', encoding='utf-8'), fieldnames=list(coins[0])); w.writeheader(); w.writerows(coins)
agg = []
for ex in EX:
    for cls in ('без', 'с тяжеловесами'):
        sub = [c for c in coins if c['exchange'] == ex and c['cls'] == cls]; tot = sum(c['turn_usd'] for c in sub) or 1
        for cov in ('ETH/Base', 'BSC(15-17.10)', 'нет в слое'):
            s = [c for c in sub if c['dex_cov'] == cov]
            sp = [c['spread_bp_snapshot'] for c in s if c['spread_bp_snapshot'] != '']
            agg.append(dict(exchange=ex, cls=cls, dex_cov=cov, n_coins=len(s), turn_usd=sum(c['turn_usd'] for c in s), share_turn=round(sum(c['turn_usd'] for c in s) / tot, 4),
                            med_spread_bp=round(statistics.median(sp), 1) if sp else '', taker=statistics.median([c['taker'] for c in sub if c['taker'] == c['taker']] or [float('nan')]),
                            maker=statistics.median([c['maker'] for c in sub if c['maker'] == c['maker']] or [float('nan')]), fee_source=EX[ex]['fee']))
w = csv.DictWriter(open(OUT + '-agg.csv', 'w', newline='', encoding='utf-8'), fieldnames=list(agg[0])); w.writeheader(); w.writerows(agg)
for a in agg: print(a['exchange'], a['cls'], a['dex_cov'], a['n_coins'], a['turn_usd'], a['share_turn'], a['med_spread_bp'], a['taker'], a['maker'])
print('top без/любой DEX:')
for ex in EX:
    for c in sorted([c for c in coins if c['exchange'] == ex and c['cls'] == 'без' and c['dex_cov'] != 'нет в слое'], key=lambda c: -c['turn_usd'])[:8]:
        print(ex, c['base'], c['turn_usd'], c['dex_nets'], c['spread_bp_snapshot'], c['limit'])
