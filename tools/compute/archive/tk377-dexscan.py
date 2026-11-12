#!/usr/bin/env python3
"""TK-377 шаг 2: для топ-N монет «без тяжеловесов» каждой биржи — есть ли пул на DEX (DexScreener search, ликвидность >= MINLIQ, символ базы совпадает).
Запуск: tk377-dexscan.py COINS_CSV OUT_CSV [N=40] [MINLIQ=50000]. Текущий снимок (23.10), не история."""
import sys, csv, json, time, urllib.request, urllib.parse
src, out = sys.argv[1], sys.argv[2]; N = int(sys.argv[3]) if len(sys.argv) > 3 else 40; MIN = float(sys.argv[4]) if len(sys.argv) > 4 else 50000
rows = [r for r in csv.DictReader(open(src, encoding='utf-8')) if r['cls'] == 'без']
seen, res = {}, []
for ex in sorted({r['exchange'] for r in rows}):
    top = sorted([r for r in rows if r['exchange'] == ex], key=lambda r: -float(r['turn_usd']))[:N]
    for r in top:
        b = r['base']
        if b not in seen:
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request('https://api.dexscreener.com/latest/dex/search?q=' + urllib.parse.quote(b), headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))
                ch = {}
                for p in d.get('pairs') or []:
                    if p['baseToken']['symbol'].upper() == b and float((p.get('liquidity') or {}).get('usd') or 0) >= MIN:
                        c = p['chainId']; l = float(p['liquidity']['usd']); v = float((p.get('volume') or {}).get('h24') or 0)
                        if c not in ch or l > ch[c][0]: ch[c] = (l, v)
                seen[b] = ch
            except Exception as e: seen[b] = None; print('ERR', b, e)
            time.sleep(0.4)
        ch = seen[b]
        res.append(dict(exchange=ex, base=b, turn_usd=r['turn_usd'], dex_chains=';'.join(f'{c}:{int(v[0])}/{int(v[1])}' for c, v in sorted((ch or {}).items(), key=lambda x: -x[1][0])) if ch is not None else 'ERR', in_layer=r['dex_cov']))
w = csv.DictWriter(open(out, 'w', newline='', encoding='utf-8'), fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
import collections
for ex in sorted({r['exchange'] for r in res}):
    s = [r for r in res if r['exchange'] == ex]; t = sum(float(r['turn_usd']) for r in s)
    has = [r for r in s if r['dex_chains'] not in ('', 'ERR')]
    cnt = collections.Counter(c.split(':')[0] for r in has for c in [r['dex_chains'].split(';')[0]])
    print(ex, 'топ', len(s), 'оборот', int(t), '| с пулом', len(has), 'оборот', int(sum(float(r['turn_usd']) for r in has)), '| главная сеть', dict(cnt))
