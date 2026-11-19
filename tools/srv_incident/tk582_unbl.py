import sys, json, glob, collections, statistics, datetime
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
cache = {}
def blocks(sh):
    if sh not in cache:
        L = []
        for l in open('%s/events-%s.jsonl' % (D, sh)):
            if not any(k in l for k in ('storm_hold', 'replace_capped', 'path_reject_capped')): continue
            try: e = json.loads(l)
            except Exception: continue
            L.append(e['ts_ms'])
        cache[sh] = L
    return cache[sh]
grp = collections.defaultdict(list)
for r in rows:
    b = [t for t in blocks(r['shard']) if r['gs'] - 300000 <= t <= r['ge']]
    g = 'trade' if r['shard'].startswith('trade') else ('mark' if r['shard'].startswith('mark') else 'book')
    grp[(g, 'blocked' if b else 'free')].append(r)
for k in sorted(grp):
    R = grp[k]; d = [r['dur'] / 1000 for r in R]; post = [(r['dur'] - max(r['t_det_ms'], 0)) / 1000 for r in R if r['t_det_ms'] is not None]
    q = lambda X, p: sorted(X)[min(len(X) - 1, int(len(X) * p))] if X else float('nan')
    print(k, 'n=%d sum=%ds dur med %.1f p90 %.1f max %.0f | post-detect (n=%d) med %.1f p90 %.1f' % (len(R), sum(d), statistics.median(d), q(d, .9), max(d), len(post), statistics.median(post) if post else float('nan'), q(post, .9)))
