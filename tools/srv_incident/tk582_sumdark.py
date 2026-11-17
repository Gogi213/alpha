import sys, json, datetime, statistics, collections
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
EPI = [('E1','21:05:50','21:06:49'),('E2','21:15:01','21:16:03'),('E3','21:25:14','21:25:53'),('E4','21:33:49','21:34:52'),('E5','21:47:48','21:48:17'),('E6','22:03:42','22:04:09'),('E7','22:15:46','22:16:14')]
STP = [('S1','21:18:32','21:21:57'),('S2','21:49:11','21:53:04'),('S3','22:19:46','22:21:13')]
rows = [json.loads(l) for l in open(sys.argv[1])]
def grp(r):
    if r['shard'].startswith('trade'): g = 'trade'
    elif r['shard'].startswith('mark'): g = 'mark'
    else: g = 'book'
    for n, a, b in STP:
        if ep(a) - 60000 <= r['gs'] <= ep(b): return n, g
    for n, a, b in EPI:
        if ep(a) - 5000 <= r['gs'] <= ep(b) + 5000: return n, g
    return 'other', g
agg = collections.defaultdict(list)
for r in rows: agg[grp(r)].append(r)
print('win grp  n  dark_s  det_med_s  rej@start(n,s)  capwait>0(n,s)  rejrtt_only(n,s)  nolog(n,s)  tdet_known(n)')
for k in sorted(agg):
    R = agg[k]; dur = sum(r['dur'] for r in R) / 1000
    det = [r['t_det_ms'] / 1000 for r in R if r['t_det_ms'] is not None]
    A = [r for r in R if r['rej_at_start']]
    B = [r for r in R if not r['rej_at_start'] and r['cap_wait_s'] > 0]
    C = [r for r in R if not r['rej_at_start'] and r['cap_wait_s'] == 0 and r['n_rejrtt'] > 0]
    N = [r for r in R if not r['rej_at_start'] and r['cap_wait_s'] == 0 and r['n_rejrtt'] == 0]
    s = lambda X: '%d,%4.0f' % (len(X), sum(r['dur'] for r in X) / 1000)
    print('%-5s %-5s %3d %6.0f  %5s   %-9s %-9s %-9s %-9s %d' % (k[0], k[1], len(R), dur, ('%.1f' % statistics.median(det)) if det else '-', s(A), s(B), s(C), s(N), len(det)))
