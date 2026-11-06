#!/usr/bin/env python3
"""TK-493 pgo: копии панелей `.sap` и фандинга ТОЛЬКО за сутки формирования (до 2479 сут от 2020-01-01 = 15.10.2026 00:00 UTC, не включая).

Для обучения PGO и замера скорости: торговое окно 15.10..04.11 в эти копии не попадает ни байтом (процесс его не читает вообще).
  prep_pform.py <корень xdl9 (~/xdl9)> <выход (~/xdl9/pgo)> [cut_day=2479]
Набор символов — из заданий gates/g6b (jobs_pair_*, jobs_basket_*, jobs_cross_*), плюс gates/g10/bin120 и byb120, gates/g6/byb50.
Выход: <out>/pform/<биржа>/<SYM>.sap, <out>/pform/bin120 и byb120 (наборы g10), <out>/pfund/<биржа>/<SYM>.csv.gz, <out>/pform/MANIFEST.txt.
Формат .sap — src/statarb/panel.rs: SAP1, u32 резерв, i64 first_min, u64 n, n×f64 open, n×f64 close (LE).
"""
import csv, glob, gzip, os, struct, sys

GRID0_MS = 1_577_836_800_000
root, out = sys.argv[1], sys.argv[2]
cut_day = int(sys.argv[3]) if len(sys.argv) > 3 else 2479
cut_min = cut_day * 1440
cut_ms = GRID0_MS + cut_min * 60_000
P, G, F = (os.path.join(root, d) for d in ("panels", "gates", "funding"))

def slice_sap(src, dst):
    with open(src, "rb") as f:
        h = f.read(24)
        magic, rsv, first, n = struct.unpack("<4sIqQ", h)
        assert magic == b"SAP1", src
        if first >= cut_min:
            return 0
        keep = min(n, cut_min - first)
        op = f.read(keep * 8)
        f.seek(24 + n * 8)
        cl = f.read(keep * 8)
    assert len(op) == keep * 8 and len(cl) == keep * 8
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as w:
        w.write(struct.pack("<4sIqQ", magic, rsv, first, keep))
        w.write(op)
        w.write(cl)
    return keep

def slice_funding(src, dst):
    rows = 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with gzip.open(src, "rt", encoding="utf-8") as r, gzip.open(dst, "wt", encoding="utf-8", newline="") as w:
        for i, line in enumerate(r):
            if i == 0:
                w.write(line)
                continue
            if int(line.split(",", 1)[0]) < cut_ms:
                w.write(line)
                rows += 1
    return rows

need = {}  # биржа -> множество символов
def add(ex, syms):
    need.setdefault(ex, set()).update(syms)

for fn in sorted(glob.glob(os.path.join(G, "g6b", "jobs_*.csv"))):
    base = os.path.basename(fn)[5:-4]  # pair_binance / basket_okx / cross_binance_bybit
    rows = list(csv.DictReader(open(fn, encoding="utf-8")))
    ys = {r["y"] for r in rows}
    xs = {s for r in rows for s in r["x"].split("+")}
    if base.startswith("cross_"):
        _, a, b = base.split("_")
        add(a, ys); add(b, xs)
    else:
        ex = base.split("_", 1)[1]
        add(ex, ys | xs)
sets = {"bin120": ("binance", os.path.join(G, "g10", "bin120")), "byb120": ("bybit", os.path.join(G, "g10", "byb120")),
        "byb50": ("bybit", os.path.join(G, "g6", "byb50"))}
named = {}
for k, (ex, d) in sets.items():
    names = sorted(f[:-4] for f in os.listdir(d) if f.endswith(".sap"))
    named[k] = (ex, names)
    add(ex, names)

man = []
tot = 0
for ex, syms in sorted(need.items()):
    for s in sorted(syms):
        src = os.path.join(P, ex, s + ".sap")
        if not os.path.exists(src):
            continue
        n = slice_sap(src, os.path.join(out, "pform", ex, s + ".sap"))
        if n:
            tot += 1
            man.append(f"{ex} {s} {n}")
        fsrc = os.path.join(F, ex, s + ".csv.gz")
        if os.path.exists(fsrc):
            slice_funding(fsrc, os.path.join(out, "pfund", ex, s + ".csv.gz"))
# наборы g10: те же файлы, только эти символы (bench берёт первые N по имени из каталога)
for k, (ex, names) in named.items():
    for s in names:
        src = os.path.join(out, "pform", ex, s + ".sap")
        if os.path.exists(src):
            dst = os.path.join(out, "pform", k, s + ".sap")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst):
                os.link(src, dst)
with open(os.path.join(out, "pform", "MANIFEST.txt"), "w") as w:
    w.write(f"cut_day {cut_day} cut_min {cut_min} cut_ms {cut_ms}\n")
    w.write("\n".join(man) + "\n")
print(f"панелей {tot}, бирж {len(need)}, обрезка по суткам < {cut_day}")
