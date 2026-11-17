# timeline of collector events in a window: tl.py <events dir> <YYYY-MM-DD> <HH:MM:SS> <HH:MM:SS> [shard-prefix]
import sys, glob, json, datetime, collections
d, day, a, b = sys.argv[1:5]; pref = sys.argv[5] if len(sys.argv) > 5 else ''
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
rows = []
for f in sorted(glob.glob(d + '/events-%s*.jsonl' % pref)):
    for l in open(f):
        if '"ts_ms":' not in l: continue
        try: e = json.loads(l)
        except Exception: continue
        if t0 <= e.get('ts_ms', 0) <= t1: rows.append(e)
rows.sort(key=lambda e: e['ts_ms'])
cnt = collections.Counter((e['shard'], e['ev']) for e in rows)
last = {}
for e in rows:
    k = (e['shard'], e['ev'], e.get('session'))
    if e['ev'] in ('replace_forced', 'slow_pong') and k in last and e['ts_ms'] - last[k] < 5000: continue
    last[k] = e['ts_ms']
    ts = datetime.datetime.fromtimestamp(e['ts_ms']/1000, datetime.timezone.utc).strftime('%H:%M:%S.%f')[:-3]
    x = {k2: v for k2, v in e.items() if k2 not in ('ev', 'shard', 'ts_ms', 'diag', 'tcp', 'url', 'entrance')}
    t = e.get('tcp')
    if t: x['tcp'] = {k2: t[k2] for k2 in ('last_data_recv_ms', 'retrans', 'rto_ms', 'rtt_ms', 'unacked', 'backoff') if k2 in t}
    print(ts, e['shard'], e['ev'], json.dumps(x, ensure_ascii=False, separators=(',', ':'))[:230])
print('--- counts'); 
for k, v in sorted(cnt.items()): print(k, v)
