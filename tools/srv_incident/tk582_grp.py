# group msgs per 10s bin (% of baseline median over the window [t-600,t-60]); grp.py <day> <from> <to> [step]
import sys, glob, json, datetime, collections, statistics
day, a, b = sys.argv[1:4]; step = int(sys.argv[4]) if len(sys.argv) > 4 else 10
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
t0, t1 = ep(a), ep(b)
D = '/data/collect/binance/data'
g = collections.defaultdict(lambda: collections.defaultdict(float)); lanes = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
prev = {}
for f in sorted(glob.glob(D + '/stats-*.jsonl')):
    n = f.split('stats-')[1].split('.')[0]; grpn = n.rstrip('0123456789')
    for l in open(f):
        if '"ts_ms"' not in l: continue
        e = json.loads(l); s = e['ts_ms'] // 1000
        if not (t0 - 700 <= s <= t1): continue
        bin_ = s // step * step
        g[grpn][bin_] += e.get('msgs', 0)
        ld = e.get('lane_delivered')
        if ld and (n, 'p') in prev:
            for k in (0, 1): lanes[grpn][bin_][k] += (ld[k] - prev[(n, 'p')][k]) > 0
        if ld: prev[(n, 'p')] = ld
for bin_ in range(t0 // step * step, t1, step):
    row = []
    for grpn in ('book', 'trade', 'mark'):
        base = [g[grpn][x] for x in range(bin_ - 600, bin_ - 60, step) if g[grpn].get(x)]
        m = statistics.median(base) if base else 0
        row.append('%s %3.0f%%' % (grpn, 100 * g[grpn][bin_] / m if m else 0))
    print(datetime.datetime.fromtimestamp(bin_, datetime.timezone.utc).strftime('%H:%M:%S'), ' | '.join(row), '| lanes alive book %d/34 trade %d/18' % (sum(lanes['book'][bin_]), sum(lanes['trade'][bin_])))
