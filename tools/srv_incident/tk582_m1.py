import sys, json, glob, collections, datetime, statistics
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
EPI = [('E','21:05:50','21:06:49'),('E','21:15:01','21:16:03'),('E','21:25:14','21:25:53'),('E','21:33:49','21:34:52'),('E','21:47:48','21:48:17'),('E','22:03:42','22:04:09'),('E','22:15:46','22:16:14')]
STP = [('S','21:18:32','21:21:57'),('S','21:49:11','21:53:04'),('S','22:19:46','22:21:13')]
def win(r):
    for n, a, b in STP:
        if ep(a) - 60000 <= r['gs'] <= ep(b): return n
    for n, a, b in EPI:
        if ep(a) - 5000 <= r['gs'] <= ep(b) + 5000: return n
    return 'other'
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
def grp(r): return 'trade' if r['shard'].startswith('trade') else ('mark' if r['shard'].startswith('mark') else 'book')
free = collections.defaultdict(list)
for r in rows:
    r['blk'] = bool([t for t in blocks(r['shard']) if r['gs'] - 300000 <= t <= r['ge']])
    if not r['blk']: free[grp(r)].append(r['dur'] / 1000)
q = lambda X, p: sorted(X)[min(len(X) - 1, int(len(X) * p))]
X90 = {g: q(v, .9) for g, v in free.items()}; X50 = {g: statistics.median(v) for g, v in free.items()}
print('free-gap p90 / median by group (s):', {g: (round(X90[g], 1), round(X50[g], 1)) for g in X90})
tab = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    g = grp(r); w = win(r); d = r['dur'] / 1000
    for key in ((w, g), (w, 'all'), ('ALL', g), ('ALL', 'all')):
        tab[key]['n'] += 1; tab[key]['dark'] += d
        if r['blk']:
            tab[key]['nblk'] += 1; tab[key]['dark_blk'] += d
            tab[key]['save90'] += max(0, d - X90[g]); tab[key]['save50'] += max(0, d - X50[g])
print('window group n dark_s | blocked n dark_s | model saved s (cap = free p90 / free median)')
for k in sorted(tab):
    t = tab[k]
    if k[1] == 'all' or k[0] == 'ALL':
        print('%-5s %-5s %3d %6.0f | %3d %6.0f | %5.0f (%2.0f%%) / %5.0f (%2.0f%%)' % (k[0], k[1], t['n'], t['dark'], t['nblk'], t['dark_blk'], t['save90'], 100 * t['save90'] / t['dark'], t['save50'], 100 * t['save50'] / t['dark']))
