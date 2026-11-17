# for each dark gap in the stops: is there a shard event (new session / reject / cap) exactly 300 s before the recovery?
import json, glob, collections, statistics, sys
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
W = [(1794863912 - 60, 1794864122), (1794865691, 1794865989), (1794867526, 1794867678)]
out = []; cache = {}
for r in rows:
    if not any(a * 1000 <= r['gs'] <= b * 1000 for a, b in W) or r['shard'].startswith('trade') or r['dur'] < 20000: continue
    sh = r['shard']
    if sh not in cache:
        L = []
        for l in open('%s/events-%s.jsonl' % (D, sh)):
            if not any(k in l for k in ('"connected"', 'path_rejected', 'replace_capped', 'storm_hold', 'path_reject_capped')): continue
            try: e = json.loads(l)
            except Exception: continue
            L.append((e['ts_ms'], e['ev']))
        cache[sh] = L
    out.append((sh, r['dur'] / 1000, cache[sh], r['ge']))
res = {}
for off in (-150, -90, -40, 0, 40, 90, 150):
    h = 0
    for sh, d, ev, ge in out:
        target = ge - 300000 + off * 1000
        if any(abs(t - target) <= 1500 for t, e in ev): h += 1
    res[off] = h
print('gaps >= 20 s:', len(out), '| hits within +-1.5 s of (recovery - 300 s + shift):', res)
