#!/usr/bin/env python3
"""TK-260: чтение готовых файлов волны (счёта нет). units <orch-dir>/units.txt  |  e2e <orch-dir> <мес-дней>
   units: порядок/разнесение единиц по суткам из units.txt (сутки k старт финиш).
   e2e:   байты по стадиям из e2e-<сутки>-<k>.jsonl: rchar = логические, read_bytes = с диска по процессу (/proc/pid/io).
   Без вложенных повторов: считаются prescan_cache + setup + day_total (events/ev_* вложены в day_total)."""
import sys, json, collections, statistics


def units(path):
    u = [(r[0], int(r[1]), float(r[2]), float(r[3])) for r in map(str.split, open(path)) if r]
    t0 = min(x[2] for x in u); T = max(x[3] for x in u) - t0
    days = sorted({x[0] for x in u}); rk = {d: i for i, d in enumerate(days)}
    byd = collections.defaultdict(list)
    for d, k, s, e in u: byd[d].append((s - t0, e - t0))
    sp = [max(s for s, _ in v) - min(s for s, _ in v) for v in byd.values()]
    first = {d: min(s for s, _ in v) for d, v in byd.items()}; last = {d: max(e for _, e in v) for d, v in byd.items()}
    ev = sorted([(first[d], 1) for d in days] + [(last[d], -1) for d in days])
    c = mx = 0; area = prev = 0.0
    for t, x in ev: area += c * (t - prev); prev = t; c += x; mx = max(mx, c)
    dist = max(abs(rk[a[0]] - rk[b[0]]) for a in u for b in u if a[2] < b[3] and b[2] < a[3])
    st = sorted(u, key=lambda x: x[2])
    back = sum(1 for i in range(1, len(st)) if rk[st[i][0]] < rk[st[i - 1][0]])
    print(f"units {len(u)} days {len(days)} wall {T:.1f}s")
    print(f"старт групп одних суток: разброс медиана {statistics.median(sp):.1f} с, макс {max(sp):.1f} с")
    print(f"сутки в работе: макс {mx}, среднее {area / T:.2f}; макс. расстояние по номеру суток среди одновременных единиц: {dist}; возвратов номера суток в порядке старта: {back}")
    print("порядок старта (первые 40):", " ".join(x[0][-2:] + "." + str(x[1]) for x in st[:40]))


def e2e(d, month="2026-01", ndays=31, G=8):
    tot = collections.defaultdict(lambda: [0, 0, 0]); pd = collections.defaultdict(lambda: [0, 0, 0]); dec = set(); n = 0
    for day in range(1, ndays + 1):
        for k in range(G):
            for l in open(f"{d}/e2e-{month}-{day:02d}-{k}.jsonl"):
                try: o = json.loads(l)
                except ValueError: continue
                if o.get("t") != "stage": continue
                s, r, b = o["stage"], o.get("rchar", 0), o.get("read_bytes", 0)
                tot[s][0] += r; tot[s][1] += b; tot[s][2] += 1
                if s in ("prescan_cache", "setup", "day_total"): pd[day][0] += r; pd[day][1] += b
                if s == "ev_decode": pd[day][2] += r; dec.add((o["sym"], o["day"]))
    for s, v in sorted(tot.items(), key=lambda x: -x[1][0]):
        print(f"{s:14s} rchar {v[0] / 1e9:8.3f} ГБ  read_bytes {v[1] / 1e9:8.3f} ГБ  n {v[2]}")
    for i, nm in enumerate(("логически (prescan_cache+setup+day_total)", "с диска по процессам", "уникально декодировано (ev_decode)")):
        xs = sorted(v[i] for v in pd.values())
        print(f"{nm}: сумма {sum(xs) / 1e9:.2f} ГБ; сутки мин {xs[0] / 1e9:.2f} мед {xs[len(xs) // 2] / 1e9:.2f} макс {xs[-1] / 1e9:.2f}")
    print(f"ev_decode: различных сутко-символов {len(dec)}, стадий {tot['ev_decode'][2]} (равно ⇒ ни один не декодирован двумя группами)")


if __name__ == "__main__":
    if sys.argv[1] == "units": units(sys.argv[2])
    else: e2e(sys.argv[2])
