#!/usr/bin/env python3
"""TK-376 проход 6, п.2: кто закрывает. Страта open_gap 50–100 бп, эпизоды, закрытые пулом (close_tx): до 10 пул-суток на сеть (по числу таких эпизодов), до 12 close_tx на пул-сутки;
receipt (from, to, status) через публичные RPC (потоки 6). Выход: addr.json (топ-10 to/from по сети, доля, число запросов/ошибок) + addr.csv. usage: addr.py episodes.csv OUT_PFX"""
import sys, csv, json, random, urllib.request, time, collections
from concurrent.futures import ThreadPoolExecutor
ep, pfx = sys.argv[1], sys.argv[2]; NETS = sys.argv[3].split(',') if len(sys.argv) > 3 else ['ethereum', 'base', 'bsc']
URL = {"ethereum": ["https://eth.drpc.org", "https://ethereum-rpc.publicnode.com"], "base": ["https://base.drpc.org", "https://mainnet.base.org"], "bsc": ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io"]}
ERR = [0]; REQ = [0]
def rpc(net, tx):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx]}).encode()
    for i in range(4):
        REQ[0] += 1
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(URL[net][i % 2], body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"}), timeout=20))
            if r.get("result") is not None: return r["result"]
        except Exception: pass
        ERR[0] += 1; time.sleep(0.5 + i)
    return None
pd = collections.defaultdict(list)
for r in csv.DictReader(open(ep)):
    if r['close_tx'] and 50 <= abs(float(r['open_gap'])) < 100 and r['cens'] == '0': pd[(r['net'], r['pool'], r['day'])].append((r['close_tx'], r['coin']))
rnd = random.Random(5); jobs = []
for net in NETS:
    ks = sorted([k for k in pd if k[0] == net], key=lambda k: -len(pd[k]))[:10]
    for k in ks:
        for tx, coin in rnd.sample(pd[k], min(12, len(pd[k]))): jobs.append((net, k[1], k[2], coin, tx))
print('jobs', len(jobs), 'pool-days', {n: len([k for k in pd if k[0] == n]) for n in ('ethereum', 'base', 'bsc')}, flush=True)
def one(j):
    rc = rpc(j[0], j[4])
    return j + ((rc['from'].lower(), (rc.get('to') or '').lower(), rc.get('status')) if rc else (None, None, None))
with ThreadPoolExecutor(6) as ex: res = list(ex.map(one, jobs))
with open(pfx + '.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['net', 'pool', 'day', 'coin', 'tx', 'from', 'to', 'status']); w.writerows(res)
out = {'requests': REQ[0], 'errors': ERR[0], 'jobs': len(jobs)}
for net in NETS:
    rs = [r for r in res if r[0] == net and r[5]]
    c_to = collections.Counter(r[6] for r in rs); c_fr = collections.Counter(r[5] for r in rs)
    out[net] = dict(n=len(rs), top_to=c_to.most_common(10), top_from=c_fr.most_common(10), share_top1_to=round(c_to.most_common(1)[0][1] / len(rs), 3) if rs else None,
                    share_top1_from=round(c_fr.most_common(1)[0][1] / len(rs), 3) if rs else None, to_eq_pool=round(sum(1 for r in rs if r[6] == r[1].lower()) / len(rs), 3) if rs else None)
json.dump(out, open(pfx + '.json', 'w'), indent=1)
print(json.dumps(out)[:1500])
