# events per minute per exchange: evmin.py <day> <from> <to>
import sys, glob, json, datetime, collections
day, a, b = sys.argv[1:4]
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
dirs = {'binance': '/data/collect/binance/data', 'bybit': '/data/turbo-probe/soak-bybit/data', 'okx': '/data/collect/okx/data', 'bingx': '/data/collect/bingx/data', 'gate': '/data/collect/gate/data'}
keys = ['timeout', 'ping_degraded', 'ws_end', 'path_rejected_rtt', 'path_rejected', 'connected', 'gap_end']
for ex, d in dirs.items():
    fs = sorted(glob.glob(d + '/events-*.jsonl'))
    c = collections.defaultdict(collections.Counter)
    for f in fs:
        for l in open(f):
            if '"ts_ms"' not in l: continue
            try: e = json.loads(l)
            except Exception: continue
            if t0 <= e['ts_ms'] < t1 and e['ev'] in keys:
                if e['ev'] == 'timeout' and e.get('fresh_data') and not (e.get('tcp') or {}).get('unacked'): continue
                c[(e['ts_ms'] - t0) // 60000][e['ev']] += 1
    print('==', ex, len(fs), 'files')
    for m in sorted(c):
        print(datetime.datetime.fromtimestamp((t0 + m*60000)/1000, datetime.timezone.utc).strftime('%H:%M'), ' '.join('%s=%d' % (k[:9], c[m][k]) for k in keys if c[m][k]))
