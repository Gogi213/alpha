import sys, json, statistics, collections, glob, datetime
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
EPI = [('E1','21:05:50','21:06:49'),('E2','21:15:01','21:16:03'),('E3','21:25:14','21:25:53'),('E4','21:33:49','21:34:52'),('E5','21:47:48','21:48:17'),('E6','22:03:42','22:04:09'),('E7','22:15:46','22:16:14')]
def epi(r):
    for n, a, b in EPI:
        if ep(a) - 5000 <= r['gs'] <= ep(b) + 5000: return n
mark = [r for r in rows if r['shard'].startswith('mark') and epi(r) and r['dur'] >= 8000]
SUB = 2.0  # model: handshake done -> subscribed -> first frame
print('mark00 gaps >= 8 s in episodes: ep dur t_det first(+s,kind,rtt/p50) rejrtt rej cap_w | saveB(accept first handshake, +2 s model) saveA(detect at 4 s / 7 s) saveAB(4 s / 7 s)')
tot = collections.Counter()
for r in mark:
    d = r['dur'] / 1000; td = r['t_det_ms'] / 1000 if r['t_det_ms'] is not None else None
    f = r['first']; ft = f[0] / 1000 if f else None
    sB = max(0, d - (ft + SUB)) if (ft is not None and f[1] != 'connected') else 0
    sA4 = max(0, td - 4) if td is not None else 0
    sA7 = max(0, td - 7) if td is not None else 0
    sAB4 = min(d, sA4 + sB); sAB7 = min(d, sA7 + sB)
    tot['dur'] += d; tot['B'] += sB; tot['A4'] += sA4; tot['A7'] += sA7; tot['AB4'] += sAB4; tot['AB7'] += sAB7
    print('%s %-3s %5.0f td=%s first=%s rejrtt=%d rej=%d cap=%d | B %4.0f A %2.0f/%2.0f AB %3.0f/%3.0f' % (datetime.datetime.fromtimestamp(r['gs']/1000, datetime.timezone.utc).strftime('%H:%M:%S'), epi(r), d, ('%.0f' % td) if td is not None else '-', ('+%.0f %s %s' % (ft, f[1], f[2])) if f else '-', r['n_rejrtt'], r['n_rej'], r['cap_wait_s'], sB, sA4, sA7, sAB4, sAB7))
print({k: round(v) for k, v in tot.items()})
