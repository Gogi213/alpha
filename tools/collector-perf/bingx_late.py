#!/usr/bin/env python3
"""«Опоздания» BingX (collector-perf, 14.11): разбор выхода bingx_late.sh. Что это: виды, лаг rx−E по гистограмме, доля по ликвидности символов (24-часовой оборот `qv` из пула, децили), россыпь против всплесков
(секунда шарда, где опоздала большая часть записей = общая задержка доставки; одиночные опоздавшие при нормальных соседях = метка биржи старше, чем доставка), близость к подписке (начальные снимки).
usage: bingx_late.py <каталог вывода bingx_late.sh> <pool-*.json> [--top N]; читает только малые файлы вывода."""
import bisect
import collections
import glob
import json
import statistics
import sys

OUT, POOL = sys.argv[1], sys.argv[2]
TOP = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 12
qv = json.load(open(POOL))["qv"]
KIND = {"b": "book", "t": "trade", "m": "mark"}
HB = ["<100", "100–200", "200–500", "500–1000", "1000–2000", "2000–3000", "3000–5000", "5000–10000", ">10000"]

n_sym = collections.defaultdict(lambda: [0, 0])      # (kind, sym) -> [n, late]
hist = collections.defaultdict(lambda: [0] * 9)
late_rows = []                                       # (kind, sym, ms, lag, et, shard)
secs = collections.defaultdict(lambda: [0, 0, 0])    # (shard, kind, sec) -> [n, late, max lag]
subs = collections.defaultdict(list)                 # shard -> [ms подписки/соединения]
for f in sorted(glob.glob(OUT + "/*.out")):
    shard = f.split("/")[-1].split("-")[0]
    for ln in open(f):
        p = ln.rstrip("\n").split("\t")
        t = p[0]
        if t == "N":
            a = n_sym[(p[1], p[2])]
            a[0] += int(p[3]); a[1] += int(p[4])
        elif t == "L":
            late_rows.append((p[1], p[2], int(p[3]), int(p[4]), p[5], shard))
        elif t == "S":
            a = secs[(shard, p[1], int(p[2]))]
            a[0] += int(p[3]); a[1] += int(p[4]); a[2] = max(a[2], int(p[5]))
        elif t == "H":
            hist[p[1]][int(p[2]) - 1] += int(p[3])
        elif t == "C" and p[2] in ("subscribed", "connected"):
            subs[shard].append(int(p[1]))
for v in subs.values():
    v.sort()

print("== виды: записей / опоздавших (>2 с) / доля")
for k in ("b", "t", "m"):
    n = sum(v[0] for (kk, s), v in n_sym.items() if kk == k)
    l = sum(v[1] for (kk, s), v in n_sym.items() if kk == k)
    print(f"{KIND[k]:6s} {n:12d} {l:8d} {100 * l / max(n, 1):.4f} %")
print("\n== распределение лага rx−E, мс (число записей по корзинам; доля)")
for k in ("b", "t", "m"):
    h = hist[k]; tot = max(sum(h), 1)
    print(KIND[k], " ".join(f"{b}: {c} ({100 * c / tot:.4f}%)" for b, c in zip(HB, h)))
print("\n== опоздавшие записи: лаг, мс (медиана / p90 / макс) и ET = E−T для book")
for k in ("b", "t", "m"):
    lg = sorted(r[3] for r in late_rows if r[0] == k)
    if lg:
        extra = ""
        if k == "b":
            ets = sorted(int(r[4]) for r in late_rows if r[0] == "b" and r[4].lstrip("-").isdigit())
            if ets:
                extra = f"; ET: p10/p50/p90 {ets[len(ets) // 10]}/{ets[len(ets) // 2]}/{ets[len(ets) * 9 // 10]} мс"
        print(f"{KIND[k]:6s} {len(lg)}: {statistics.median(lg):.0f} / {lg[int(len(lg) * .9)]} / {lg[-1]}{extra}")

# ликвидность: децили по qv среди символов пула (десятая — самая ликвидная)
syms = sorted(qv, key=lambda s: qv[s])
dec = {s: min(9, i * 10 // len(syms)) for i, s in enumerate(syms)}
print("\n== по ликвидности (децили 24-часового оборота qv; 0 — самые неликвидные): символов / оборот мин–макс, $ / book late (доля на запись) / mark late / trade late")
for d in range(10):
    ss = [s for s in syms if dec[s] == d]
    row = [f"{d}: {len(ss)} симв., {qv[ss[0]]:,.0f}–{qv[ss[-1]]:,.0f}"]
    for k in ("b", "m", "t"):
        n = sum(n_sym.get((k, s), [0, 0])[0] for s in ss)
        l = sum(n_sym.get((k, s), [0, 0])[1] for s in ss)
        row.append(f"{l} ({100 * l / max(n, 1):.4f}%)")
    print("  ".join(row))
print("\n== самые частые символы среди опоздавших (book и mark): символ, оборот, поздних / записей")
for k in ("b", "m"):
    top = sorted(((v[1], s) for (kk, s), v in n_sym.items() if kk == k and v[1]), reverse=True)[:TOP]
    print(KIND[k], "; ".join(f"{s} ({qv.get(s, 0):,.0f}$) {l}/{n_sym[(k, s)][0]}" for l, s in top))
    nsym = sum(1 for (kk, s), v in n_sym.items() if kk == k and v[1])
    print(f"   символов с хотя бы одной поздней записью: {nsym} из {sum(1 for (kk, s) in n_sym if kk == k)}")

# всплески: секунда шарда, в которой поздних ≥ 50 % записей этого вида (и ≥ 5 записей)
burst = {(sh, k, sec) for (sh, k, sec), (n, l, m) in secs.items() if n >= 5 and l * 2 >= n}
tot_late = collections.Counter(); in_burst = collections.Counter()
for k, s, ms, lag, et, sh in late_rows:
    tot_late[k] += 1
    kk = {"b": "b", "t": "t", "m": "m"}[k]
    if (sh, kk, ms // 1000) in burst or (sh, kk, ms // 1000 - 1) in burst or (sh, kk, ms // 1000 + 1) in burst:
        in_burst[k] += 1
print("\n== россыпь или всплески: опоздавшие записи в секунду шарда (±1 с), где поздних ≥ половины записей = общая задержка доставки")
for k in ("b", "m", "t"):
    if tot_late[k]:
        print(f"{KIND[k]:6s} в всплесках {in_burst[k]} из {tot_late[k]} ({100 * in_burst[k] / tot_late[k]:.1f} %), одиночные {tot_late[k] - in_burst[k]}")
# число поздних в секунду (сразу у соседей): сколько секунд с поздними и сколько среди них с одной
cnt = collections.Counter(l for (sh, k, sec), (n, l, m) in secs.items())
print("секунд шарда с поздними записями по числу поздних в секунде:", dict(sorted(cnt.items())[:8]), "… макс", max(cnt) if cnt else 0)

# близость к подписке: сколько опоздавших в первые 15 с после connected/subscribed того же шарда
near = collections.Counter()
for k, s, ms, lag, et, sh in late_rows:
    v = subs.get(sh, [])
    i = bisect.bisect_right(v, ms) - 1
    if i >= 0 and ms - v[i] <= 15_000:
        near[k] += 1
print("\n== опоздавшие в первые 15 с после connected/subscribed шарда (начальные снимки со старой меткой):", {KIND[k]: f"{near[k]} из {tot_late[k]}" for k in tot_late})

# по часам
byh = collections.defaultdict(lambda: collections.Counter())
import time
for k, s, ms, lag, et, sh in late_rows:
    byh[time.strftime("%H", time.gmtime(ms / 1000))][k] += 1
print("\n== по часам UTC (book / mark / trade):", "  ".join(f"{h}: {c['b']}/{c['m']}/{c['t']}" for h, c in sorted(byh.items())))
# «возраст» метки: у опоздавшей записи лаг как доля: ровные 5000/10000 → таймеры биржи?
lags = collections.Counter(round(r[3] / 1000) for r in late_rows)
print("лаг опоздавших, округление до секунд:", dict(sorted(lags.items())[:14]))
