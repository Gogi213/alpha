# bytes/s and lag by group per bin: bw.py <day> <from> <to> <step>
import sys, glob, json, datetime, collections, statistics
day, a, b, step = sys.argv[1:5]; step = int(step)
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
t0, t1 = ep(a), ep(b)
D = '/data/collect/binance/data'
by = collections.defaultdict(lambda: collections.defaultdict(float)); lag = collections.defaultdict(lambda: collections.defaultdict(list)); rq = collections.defaultdict(lambda: collections.defaultdict(float))
for f in sorted(glob.glob(D + '/stats-*.jsonl')):
    n = f.split('stats-')[1].split('.')[0]; gp = n.rstrip('0123456789')
    for l in open(f):
        if '"ts_ms"' not in l: continue
        e = json.loads(l); s = e['ts_ms'] // 1000
        if not (t0 <= s < t1): continue
        k = s // step * step
        by[gp][k] += e.get('bytes', 0); lag[gp][k].append(e.get('lag_p50_ms', 0) or 0); rq[gp][k] = max(rq[gp][k], e.get('recvq_max', 0))
for k in range(t0 // step * step, t1, step):
    print(datetime.datetime.fromtimestamp(k, datetime.timezone.utc).strftime('%H:%M:%S'), ' | '.join('%s %5.0f kB/s lagmax %5.1fs rq %dkB' % (gp, by[gp][k] / step / 1000, max(lag[gp][k] or [0]) / 1000, rq[gp][k] / 1000) for gp in ('book', 'trade', 'mark')))
