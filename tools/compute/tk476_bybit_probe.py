#!/usr/bin/env python3
"""TK-476: замер Bybit — REST kline 1m против дневного архива сделок public.bybit.com на одних сутках. Использование: tk476_bybit_probe.py ДЕНЬ СИМВОЛ…"""
import csv, gzip, io, json, sys, time, urllib.request, datetime as dt

def get(u):
    with urllib.request.urlopen(u, timeout=120) as r:
        return r.read()

def rest(sym, d0):
    s = int(dt.datetime.strptime(d0, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    out, t0, nreq = {}, time.time(), 0
    for st in (s, s + 1000 * 60000):
        x = json.loads(get(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={st}&end={st + 999 * 60000}&limit=1000'))
        nreq += 1
        for r in x['result']['list']:
            if s <= int(r[0]) < s + 86400000:
                out[int(r[0])] = tuple(r[1:6])
    return out, time.time() - t0, nreq

def arch(sym, d0):
    t0 = time.time()
    raw = get(f'https://public.bybit.com/trading/{sym}/{sym}{d0}.csv.gz')
    rows = list(csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), 'ascii')))[1:]
    rows.sort(key=lambda r: float(r[0]))
    m = {}
    for r in rows:
        k = int(float(r[0]) // 60) * 60000
        px, q = float(r[4]), float(r[3])
        c = m.get(k)
        if c is None:
            m[k] = [r[4], px, px, r[4], q, 1]
        else:
            c[1] = max(c[1], px); c[2] = min(c[2], px); c[3] = r[4]; c[4] += q; c[5] += 1
    return m, len(raw), len(rows), time.time() - t0

d0 = sys.argv[1]
for sym in sys.argv[2:]:
    a, nb, nr, ta = arch(sym, d0)
    r, tr, nq = rest(sym, d0)
    both = set(a) & set(r)
    eo = sum(abs(a[k][0] != r[k][0] and float(a[k][0]) != float(r[k][0])) for k in both)
    ec = sum(float(a[k][3]) != float(r[k][3]) for k in both)
    eh = sum(abs(a[k][1] - float(r[k][1])) > 1e-9 for k in both)
    ev = sum(abs(a[k][4] - float(r[k][4])) > 1e-6 * max(1, a[k][4]) for k in both)
    print(f'{sym} {d0}: архив {nb / 1e6:.1f} МБ {nr} сделок {ta:.1f} с; REST {nq} запр {tr:.1f} с; минут архив {len(a)} REST {len(r)} общих {len(both)}; '
          f'расхождений o={eo} h={eh} c={ec} v={ev}')
