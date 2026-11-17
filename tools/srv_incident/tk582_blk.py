# classify blockers on the sessions alive at gap start: blk.py <jsonl> -> per window summary
import sys, glob, json, collections, datetime
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
WIN = [('S1','21:17:30','21:22:00'),('S2','21:48:50','21:53:10'),('S3','22:19:00','22:21:20')]
def ln(s): return 0 if s < 1795000000 else 1
cache = {}
def evs(sh):
    if sh not in cache:
        L = []
        for l in open('%s/events-%s.jsonl' % (D, sh)):
            if '"ts_ms"' not in l: continue
            try: e = json.loads(l)
            except Exception: continue
            if 1794863000000 <= e['ts_ms'] <= 1794868500000: L.append(e)
        cache[sh] = L
    return cache[sh]
out = collections.defaultdict(list)
for r in rows:
    w = next((n for n, a, b in WIN if ep(a) <= r['gs'] <= ep(b)), None)
    if not w or r['shard'].startswith('trade'): continue
    ev = evs(r['shard'])
    blk = collections.Counter()
    for e in ev:
        if e['ev'] in ('replace_capped', 'storm_hold', 'path_reject_capped') and r['gs'] - 300000 <= e['ts_ms'] <= r['ge']:
            blk[e['ev']] += 1
    out[w].append((r, dict(blk)))
for w in out:
    R = out[w]; tot = sum(r['dur'] for r, b in R) / 1000
    def cnt(f):
        X = [r for r, b in R if f(r, b)]
        return '%d gaps %ds' % (len(X), sum(r['dur'] for r in X) / 1000)
    print(w, 'all', cnt(lambda r, b: True))
    print('   storm_hold in [gs-300s,ge]:', cnt(lambda r, b: 'storm_hold' in b))
    print('   replace_capped:', cnt(lambda r, b: 'replace_capped' in b))
    print('   path_reject_capped:', cnt(lambda r, b: 'path_reject_capped' in b))
    print('   any of the three:', cnt(lambda r, b: bool(b)))
    print('   none logged:', cnt(lambda r, b: not b and not r['rej_at_start']))
    print('   reject at start (partner dead):', cnt(lambda r, b: r['rej_at_start']))
