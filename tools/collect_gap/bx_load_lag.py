#!/usr/bin/env python3
"""BingX: нагрузка шарда (кадров/с) и лаг p50 по часам UTC из stats-<шард>.jsonl; до/после смены раскладки; корреляция нагрузка -> лаг.
Запуск: bx_load_lag.py <корень> [t0_ms]
Кадры = msgs окна 10 с / 10 (по обеим линиям шарда); лаг по часу = среднее p50 окон, взвешенное msgs."""
import collections, glob, json, math, os, sys, time

root = sys.argv[1]
t0 = int(sys.argv[2]) if len(sys.argv) > 2 else 0
STARTS = {'813d2fef': 1794617580, 'c62d69aa': 1794656729, '9c44e141': 1794794011, '3835be7b': 1794812547}  # с epoch, события `started`
agg = collections.defaultdict(lambda: [0, 0.0, 0, []])  # (шард, час) -> [msgs, sum p50*msgs, окон, [p99]]
for f in sorted(glob.glob(os.path.join(root, 'stats-*.jsonl'))):
    shard = os.path.basename(f)[6:-6]
    if shard.startswith('mark'):
        continue
    with open(f, 'rb') as fh:
        size = os.path.getsize(f)
        fh.seek(max(0, size - 60_000_000))
        for ln in fh:
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            ts, p50, w = v.get('ts_ms'), v.get('lag_p50_ms'), v.get('msgs') or 0
            if ts is None or ts < t0 or p50 is None or w <= 0:
                continue
            a = agg[(shard, int(ts // 3_600_000))]
            a[0] += w
            a[1] += p50 * w
            a[2] += 1
            if v.get('lag_p99_ms') is not None:
                a[3].append(v['lag_p99_ms'])

def run_of(h):  # час -> версия, действовавшая в начале часа (часы со сменой помечаются по версии на конец часа)
    ts = h * 3600 + 3599
    cur = '?'
    for k, s in sorted(STARTS.items(), key=lambda kv: kv[1]):
        if ts >= s:
            cur = k
    return cur

shards = sorted({s for s, _ in agg})
hours = sorted({h for _, h in agg})

def per(shard, hs):
    m = sum(agg[(shard, h)][0] for h in hs if (shard, h) in agg)
    n = sum(agg[(shard, h)][2] for h in hs if (shard, h) in agg)
    l = sum(agg[(shard, h)][1] for h in hs if (shard, h) in agg)
    return (m / (n * 10.0) if n else None, l / m if m else None, n)

def hh(h):
    return time.strftime('%d %H', time.gmtime(h * 3600))

# периоды: до — 02..06 UTC 16.11 (9c44e141, алфавитная раскладка), после — полные часы 08..последний (3835be7b, раскладка по обороту)
def hrange(day_hour_from, day_hour_to):
    a = int(time.mktime(time.strptime(day_hour_from, '%Y-%m-%d %H')) - time.timezone) // 3600
    b = int(time.mktime(time.strptime(day_hour_to, '%Y-%m-%d %H')) - time.timezone) // 3600
    return [h for h in hours if a <= h <= b]

BEFORE = hrange('2026-11-16 02', '2026-11-16 06')
AFTER = hrange('2026-11-16 08', '2026-11-16 23')
print('до: часы', [hh(h) for h in BEFORE], ' после: часы', [hh(h) for h in AFTER])
print('%-8s %12s %12s %7s | %9s %9s %7s' % ('шард', 'кадр/с до', 'кадр/с после', 'x', 'лаг до', 'лаг после', 'дельта'))
rows = {}
for s in shards:
    b, a = per(s, BEFORE), per(s, AFTER)
    rows[s] = (b, a)
    if b[0] and a[0]:
        print('%-8s %12.1f %12.1f %7.2f | %9.1f %9.1f %+7.1f' % (s, b[0], a[0], a[0] / b[0], b[1], a[1], a[1] - b[1]))
# нагрузка до/после по видам: доля шарда в сумме вида
for kind in ('book', 'trade'):
    ks = [s for s in shards if s.startswith(kind)]
    tb = sum(rows[s][0][0] or 0 for s in ks)
    ta = sum(rows[s][1][0] or 0 for s in ks)
    print(kind, 'сумма кадр/с до %.1f после %.1f; доли до' % (tb, ta), ['%.3f' % ((rows[s][0][0] or 0) / tb) for s in ks], 'после', ['%.3f' % ((rows[s][1][0] or 0) / ta) for s in ks])

def pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else None

def ranks(v):
    o = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for k, i in enumerate(o):
        r[i] = k
    return r

for kind in ('trade', 'book'):
    pts = []  # (кадр/с шарда-часа, лаг p50 шарда-часа, шард, час)
    for (s, h), a in agg.items():
        if s.startswith(kind) and a[2] >= 300 and a[0] > 0:  # час не короче 50 мин
            pts.append((a[0] / (a[2] * 10.0), a[1] / a[0], s, h))
    x, y = [p[0] for p in pts], [p[1] for p in pts]
    print(f'{kind}: точек шард-час {len(pts)}; Pearson(кадр/с, лаг p50) = {pearson(x, y)}; Spearman = {pearson(ranks(x), ranks(y))}')
    # внутри часа между шардами: центрируем по часу (убирает общий для часа уровень лага) — и внутри шарда по времени (убирает свойство шарда)
    byh = collections.defaultdict(list)
    for p in pts:
        byh[p[3]].append(p)
    xs, ys = [], []
    for h, ps in byh.items():
        if len(ps) >= 3:
            mx = sum(p[0] for p in ps) / len(ps)
            my = sum(p[1] for p in ps) / len(ps)
            xs += [p[0] - mx for p in ps]
            ys += [p[1] - my for p in ps]
    print(f'   между шардами внутри часа (центр по часу): n={len(xs)} Pearson = {pearson(xs, ys)}')
    bys = collections.defaultdict(list)
    for p in pts:
        bys[p[2]].append(p)
    xs, ys = [], []
    for s, ps in bys.items():
        if len(ps) >= 3:
            mx = sum(p[0] for p in ps) / len(ps)
            my = sum(p[1] for p in ps) / len(ps)
            xs += [p[0] - mx for p in ps]
            ys += [p[1] - my for p in ps]
    print(f'   внутри шарда во времени (центр по шарду): n={len(xs)} Pearson = {pearson(xs, ys)}')
    # наклон лаг на кадр/с (МНК), мс на 100 кадр/с
    n = len(x)
    if n > 2:
        mx, my = sum(x) / n, sum(y) / n
        den = sum((a - mx) ** 2 for a in x)
        print(f'   МНК: лаг = {my - (sum((a - mx) * (b - my) for a, b in zip(x, y)) / den) * mx:.1f} + {sum((a - mx) * (b - my) for a, b in zip(x, y)) / den * 100:.2f} мс на 100 кадр/с; кадр/с: min {min(x):.0f} med {sorted(x)[n // 2]:.0f} max {max(x):.0f}')
# по часам трейды: кадр/с и лаг по шардам
print('часы (сделки): шард: кадр/с / лаг p50')
ts = [s for s in shards if s.startswith('trade')]
print('час   версия    ' + ' '.join('%-12s' % s for s in ts))
for h in hours[-48:]:
    cells = []
    for s in ts:
        a = agg.get((s, h))
        cells.append('%-12s' % ('%.0f/%.0f' % (a[0] / (a[2] * 10.0), a[1] / a[0]) if a and a[2] >= 30 else '-'))
    print(hh(h), run_of(h), ' '.join(cells))
