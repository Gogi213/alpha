#!/usr/bin/env python3
"""TK-374: fee tier пулов банка (GeckoTerminal pool_fee_percentage). usage: tk374-fees.py pools.csv out.json"""
import sys, csv, json, time, urllib.request
NET = {'ethereum': 'eth', 'base': 'base'}
out = {}
for r in csv.DictReader(open(sys.argv[1], encoding='utf-8-sig')):
    if r['chain'] not in NET: continue
    k = f"{r['coin']}@{r['chain']}"
    for att in range(4):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.geckoterminal.com/api/v2/networks/{NET[r['chain']]}/pools/{r['pool']}", headers={'accept': 'application/json'}), timeout=30))['data']['attributes']
            out[k] = {'pool': r['pool'], 'fee': d.get('pool_fee_percentage'), 'name': d.get('name'), 'dex': r['dex'], 'labels': r['labels']}; break
        except Exception as e:
            time.sleep(5 * (att + 1))
    else: out[k] = {'pool': r['pool'], 'fee': None, 'err': 1}
    time.sleep(2.2)
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
print(len(out), sum(1 for v in out.values() if v['fee'] is None), 'без fee')
for k, v in out.items(): print(k, v['fee'], v.get('name'))
