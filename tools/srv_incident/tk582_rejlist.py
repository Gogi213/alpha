import json, glob, sys, datetime
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
def hms(ms): return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%H:%M:%S')
print('shard gap_start dur_s | path_rejected near gap start: n p50_ms p99_ms | wait_s of path_reject_capped right after')
tot = 0
for r in rows:
    if not r['rej_at_start']: continue
    ev = []
    for l in open('%s/events-%s.jsonl' % (D, r['shard'])):
        if 'path_rejected"' not in l and 'path_reject_capped' not in l: continue
        e = json.loads(l)
        if r['gs'] - 1500 <= e['ts_ms'] <= r['gs'] + 2500: ev.append(e)
    rj = [e for e in ev if e['ev'] == 'path_rejected']; cp = [e for e in ev if e['ev'] == 'path_reject_capped']
    tot += r['dur']
    print(r['shard'], hms(r['gs']), '%.0f' % (r['dur']/1000), '|', ' '.join('n=%s p50=%s p99=%s' % (e.get('n'), e.get('p50_ms'), e.get('p99_ms')) for e in rj), '|', ' '.join(str(e.get('wait_s')) for e in cp))
print('total dark s', tot // 1000)
