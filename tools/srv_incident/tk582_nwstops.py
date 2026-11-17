import json, calendar, time, sys
# TK-582 v2: проба netwatch `binance` (TCP-connect к 13.193.198.54, AWS Токио) в окнах трёх остановок и пяти минут до них; потеря = rtt null или > 700 мс (SYN_MS страницы здоровья)
day = sys.argv[1]; f = '/data/netwatch/%s.jsonl' % day
def t(h, m, s=0): return calendar.timegm((2026, 11, 16, h, m, s))
WIN = [('S1', t(21, 13), t(21, 24)), ('S2', t(21, 45), t(21, 56)), ('S3', t(22, 14), t(22, 25))]
rows = []
for l in open(f):
    r = json.loads(l)
    if r.get('n') == 'binance' and 'ts' in r: rows.append((r['ts'], r.get('rtt')))
for n, a, b in WIN:
    w = [x for x in rows if a <= x[0] < b]
    lost = [x for x in w if x[1] is None or x[1] > 700]
    cl = []
    for x in lost:
        if cl and x[0] - cl[-1][1] <= 3: cl[-1][1] = x[0]; cl[-1][2] += 1
        else: cl.append([x[0], x[0], 1])
    print('%s %s–%s UTC: замеров %d, потерь %d (%.1f%%), всего секунд в кластерах %d' % (n, time.strftime('%H:%M', time.gmtime(a)), time.strftime('%H:%M', time.gmtime(b)), len(w), len(lost), 100 * len(lost) / max(1, len(w)), sum(c[2] for c in cl)))
    print('   кластеры потерь (начало, потерянных замеров, длина с): ' + ', '.join('%s %d/%d' % (time.strftime('%H:%M:%S', time.gmtime(c[0])), c[2], c[1] - c[0] + 1) for c in cl))
