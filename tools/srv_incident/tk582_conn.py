# connection attempts proxy: connected + path_rejected_rtt + error(handshake/connect) per sliding 300 s, Binance 21:00-22:30
import glob, json, collections, datetime, bisect
D = '/data/collect/binance/data'
T0 = 1794862800000; T1 = 1794868200000 + 300000
ts = []; kinds = collections.Counter()
for f in glob.glob(D + '/events-*.jsonl'):
    for l in open(f):
        if '"ts_ms"' not in l: continue
        if not any(k in l for k in ('"connected"', '"path_rejected_rtt"', '"error"')): continue
        e = json.loads(l)
        if T0 <= e['ts_ms'] <= T1 and e['ev'] in ('connected', 'path_rejected_rtt', 'error'):
            if e['ev'] == 'error' and not any(w in e.get('err', '') for w in ('рукопожатие', 'подключ', 'connect', 'tls', 'TLS', 'resolve')): continue
            ts.append(e['ts_ms']); kinds[e['ev']] += 1
ts.sort(); print(kinds)
best = (0, 0)
for i, t in enumerate(ts):
    j = bisect.bisect_right(ts, t)
    n = j - bisect.bisect_left(ts, t - 300000)
    if n > best[0]: best = (n, t)
print('max in any 300s window (proxy):', best[0], datetime.datetime.fromtimestamp(best[1]/1000, datetime.timezone.utc).strftime('%H:%M:%S'))
for lab, a, b in (('S1', 1794863912, 1794864117), ('S2', 1794865751, 1794865984), ('S3', 1794867586, 1794867673)):
    ws = [ (bisect.bisect_right(ts, t*1000) - bisect.bisect_left(ts, t*1000 - 300000)) for t in range(a, b, 10)]
    print(lab, 'proxy in 300s window during stop: min %d max %d' % (min(ws), max(ws)))
