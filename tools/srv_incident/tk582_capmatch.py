import sys, json, glob
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
ok = tot = 0; lines = []
cache = {}
for r in rows:
    if r['dur'] < 20000: continue
    sh = r['shard']
    if sh not in cache:
        cache[sh] = []
        for l in open('%s/events-%s.jsonl' % (D, sh)):
            if 'path_reject_capped' not in l and '"connected"' not in l: continue
            try: cache[sh].append(json.loads(l))
            except Exception: pass
    caps = [(e['ts_ms'], e['wait_s']) for e in cache[sh] if e['ev'] == 'path_reject_capped' and r['gs'] - 60000 <= e['ts_ms'] <= r['ge']]
    if not caps: continue
    t, w = max(caps, key=lambda c: c[0] + c[1] * 1000)
    pred = t + w * 1000
    tot += 1; d = (r['ge'] - pred) / 1000
    if abs(d) <= 10: ok += 1
    lines.append('%s gap %.0fs cap_wait_until_end_of_gap: pred_release-ge = %+.0f s (wait %ds)' % (sh, r['dur']/1000, -d, w))
print('gaps >=20 s with a path_reject_capped event:', tot, '; recovery within +-10 s of the cap release:', ok)
print('\n'.join(lines[:40]))
