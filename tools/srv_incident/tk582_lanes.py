# mark00 + book lanes liveness per 10 s slot in episodes/stops: lanes.py
import json, glob, datetime, collections
D = '/data/collect/binance/data'
T0, T1 = 1794862800000, 1794868200000
def load(n):
    prev = None; out = []
    for l in open('%s/stats-%s.jsonl' % (D, n)):
        if '"ts_ms"' not in l: continue
        e = json.loads(l); ld = e.get('lane_delivered')
        if prev is not None and T0 <= e['ts_ms'] <= T1 and ld: out.append((e['ts_ms'], ld[0] - prev[0], ld[1] - prev[1], e.get('msgs', 0)))
        prev = ld
    return out
EPI = [('E1',1794863150,1794863210),('E2',1794863701,1794863763),('E3',1794864314,1794864353),('E4',1794864829,1794864892),('E5',1794865668,1794865697),('E6',1794866622,1794866649),('E7',1794867346,1794867374)]
for n in ['mark00']:
    rows = load(n)
    print(n, 'slots', len(rows))
    for lab, a, b in EPI:
        R = [r for r in rows if a - 10 <= r[0] / 1000 <= b + 25]
        c = collections.Counter((r[1] > 0, r[2] > 0) for r in R)
        print(lab, 'slots', len(R), 'L0only', c[(True, False)], 'L1only', c[(False, True)], 'both', c[(True, True)], 'none', c[(False, False)])
    R = rows; c = collections.Counter((r[1] > 0, r[2] > 0) for r in R)
    print('all 21:00-22:30', len(R), 'L0only', c[(True, False)], 'L1only', c[(False, True)], 'both', c[(True, True)], 'none', c[(False, False)])
