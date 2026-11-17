import glob, json, collections, datetime
D = '/data/collect/binance/data'
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
W = [('S1', '21:18:20', '21:22:00'), ('S2', '21:49:00', '21:53:10'), ('S3', '22:19:30', '22:21:20'), ('E1-7', '21:05:40', '21:06:59'), ('E2-7', '21:14:55', '21:16:20'), ('E3', '21:25:00', '21:26:10'), ('E4', '21:33:40', '21:35:00'), ('E5', '21:47:40', '21:48:40'), ('E6', '22:03:30', '22:04:30'), ('E7', '22:15:30', '22:16:40')]
res = collections.defaultdict(collections.Counter)
for f in glob.glob(D + '/events-*.jsonl'):
    n = f.split('events-')[1].split('.')[0]; g = n.rstrip('0123456789')
    for l in open(f):
        if '"timeout"' not in l: continue
        try: e = json.loads(l)
        except Exception: continue
        if e.get('ev') != 'timeout' or e.get('fresh_data'): continue
        t = e.get('tcp') or {}
        for w, a, b in W:
            if ep(a) <= e['ts_ms'] <= ep(b):
                k = 'blackhole(un>=1,bo>=1)' if (t.get('unacked') or 0) >= 1 and (t.get('backoff') or 0) >= 1 else ('acked-but-silent(un=0)')
                res[w][g + ' ' + k] += 1
for w, _, _ in W:
    print(w, dict(res[w]))
