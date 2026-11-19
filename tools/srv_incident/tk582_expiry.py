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
    target = r['ge'] - 300000
    near = min(((abs(t - target), t - target, ev) for t, ev in cache[sh] if abs(t - target) <= 30000), default=None)
    out.append((sh, r['dur'] / 1000, near))
hit = [o for o in out if o[2] and o[2][0] <= 6000]
print('dark gaps >= 20 s in the three stops (book+mark):', len(out), '| with a shard event within +-6 s of (recovery - 300 s):', len(hit), '| within +-30 s:', sum(1 for o in out if o[2]))
for sh, d, n in out: print(sh, '%.0fs' % d, ('%+.1fs %s' % (n[1] / 1000, n[2])) if n else '-')
