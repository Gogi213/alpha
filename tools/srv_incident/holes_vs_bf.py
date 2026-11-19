#!/usr/bin/env python3
"""TK-581: дыры в номерах сделок по сырым файлам ws (`holes` из coverage.py, до дозапроса) против строк `rt` дозапроса REST (`backfill-<биржа>-*.tsv.zst`) —
свой пересчёт потери сделок вместо итога коллектора. Остаток = номера, которых нет ни в записях ws, ни в `rt` (интервалы вычитаются).
Binance: дыры — по номерам сделок f..l (в файле сделок столбцы 9 и 8), `rt` — столбцы 7 и 8 (первая и последняя сделка агрегата) по символу.
OKX и BingX: дозапрос в окне пуст (если нет `rt`-строк) — остаток = дыры.
python3 holes_vs_bf.py <cov-*.json> [день=2026-11-16] — на srv, читает /data/collect/<биржа>/data/<день>/backfill-*.tsv.zst"""
import json, sys, glob, subprocess, collections, datetime as dt

ROOTS = {"binance": "/data/collect/binance/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data"}
fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%H:%M:%S")
path = sys.argv[1]
day = sys.argv[2] if len(sys.argv) > 2 else "2026-11-16"
d = json.load(open(path))
ex = d["exchange"]
holes = collections.defaultdict(list)  # sym -> [lo, hi, created_ms]
for f in d["files"]:
    for s, hs in f["holes"].items():
        holes[s].extend(hs)
rt = collections.defaultdict(list)     # sym -> [(first, last)]
nrt = nrg = 0
for p in sorted(glob.glob(f"{ROOTS[ex]}/{day}/backfill-*.tsv.zst")):
    out = subprocess.run(["zstd", "-dcq", p], capture_output=True).stdout.decode("utf-8", "replace")
    for ln in out.split("\n"):
        q = ln.split("\t")
        if q[0] == "rt" and len(q) >= 9:
            try:
                rt[q[2]].append((int(q[7]), int(q[8])))
                nrt += 1
            except ValueError:
                pass
        elif q[0] == "rg":
            nrg += 1


def subtract(lo, hi, ivs):
    cur = [(lo, hi)]
    for a, b in ivs:
        nxt = []
        for x, y in cur:
            if b < x or a > y:
                nxt.append((x, y)); continue
            if a > x:
                nxt.append((x, a - 1))
            if b < y:
                nxt.append((b + 1, y))
        cur = nxt
        if not cur:
            break
    return cur


tot_ids = tot_rem = 0
rem_rows = []
byminute = collections.Counter()
byminute_rem = collections.Counter()
for s, hs in holes.items():
    ivs = sorted(rt.get(s, []))
    for lo, hi, cr in hs:
        n = hi - lo + 1
        rem = subtract(lo, hi, ivs)
        r = sum(y - x + 1 for x, y in rem)
        tot_ids += n
        tot_rem += r
        m = fmt(cr)[:5]
        byminute[m] += n
        byminute_rem[m] += r
        if r:
            rem_rows.append((fmt(cr), s, lo, hi, n, r, cr))
print(f"### {ex}: дыр в номерах сделок на конец окна скана (до дозапроса): символов {len(holes)}, интервалов {sum(len(v) for v in holes.values())}, номеров {tot_ids}")
print(f"  строк дозапроса за день: rg {nrg}, rt {nrt} (символов {len(rt)})")
print(f"  остаток после вычитания rt: {tot_rem} номеров ({100.0 * tot_rem / tot_ids:.2f} % от дыр)" if tot_ids else "  дыр нет — остаток 0")
for m in sorted(byminute):
    print(f"  минута создания дыры {m}: номеров {byminute[m]}, остаток {byminute_rem[m]}")
for r in sorted(rem_rows, key=lambda x: x[6]):
    print("  остаток:", r[0], r[1], r[2], r[3], r[4], r[5], r[6])
