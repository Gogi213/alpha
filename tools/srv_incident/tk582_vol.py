# per-shard pre-window rate vs dark seconds in window: vol.py <day> <pre_from> <pre_to> <from> <to>
import sys, glob, json, datetime
day, p0, p1, a, b = sys.argv[1:6]
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
P0, P1, T0, T1 = ep(p0), ep(p1), ep(a), ep(b)
D = '/data/collect/binance/data'
for f in sorted(glob.glob(D + '/stats-book*.jsonl')) + sorted(glob.glob(D + '/stats-trade*.jsonl')) + sorted(glob.glob(D + '/stats-mark*.jsonl')):
    n = f.split('stats-')[1].split('.')[0]
    by = 0; k = 0; msgs = 0
    for l in open(f):
        if '"ts_ms"' not in l: continue
        e = json.loads(l)
        if P0 <= e['ts_ms'] < P1: by += e.get('bytes', 0); k += 1; msgs += e.get('msgs', 0)
    dark = 0; ng = 0
    for l in open(D + '/events-%s.jsonl' % n):
        if '"gap_end"' not in l: continue
        e = json.loads(l)
        if e.get('ev') == 'gap_end' and 'gap_end_ms' in e and T0 <= e['gap_end_ms'] <= T1 and e['gap_total_ms'] >= 3000: dark += e['gap_total_ms']; ng += 1
    print('%-7s pre %6.0f kB/s %6.0f msg/s | dark %4ds (%d gaps)' % (n, by / max(k, 1) / 10 / 1000, msgs / max(k, 1) / 10, dark // 1000, ng))
