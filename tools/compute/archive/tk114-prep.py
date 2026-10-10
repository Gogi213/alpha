#!/usr/bin/env python3
"""TK-114 ярус 1 (стоп 3/4 %): сырой прогон `--busy-skip off` B1 (TK-065 w-/ws-) и 7 клеток П-12 (TK-084 w-) -> дерево
/data/tk0114/off/<мес>/<сутки>/t-bid-btc4h-q1/{rounds,signals}.csv (8 форм, символы суток — из p12 forms.csv).
Дальше busy-replay.py (по месяцу) и portfolio-sim.py. Использование: tk083-kpi-prep.py [месяц ...]"""
import csv, os, sys

B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
FORMS = {B1,
         "ladder3x0..0.0409sw2-pct3-tr1x1-14400-ttl1800",
         "ladder3x0..0.0409sw2-pct4-tr1x1-14400-ttl1800"}
SET = "t-bid-btc4h-q1"
OUT = "/data/tk0114/off"
want = set(sys.argv[1:])


def rd(f):
    if not os.path.exists(f):
        return [], []
    L = open(f, encoding="utf-8").read().splitlines()
    return [l for l in L if l.startswith("#")], list(csv.DictReader(l for l in L if not l.startswith("#")))


def fields(f):
    return next(l for l in open(f, encoding="utf-8").read().splitlines() if not l.startswith("#")).split(",")


for ln in open("/data/tk065/days/days.tsv"):
    d, m = ln.split()
    if want and d[:7] not in want:
        continue
    pdir = f"/data/tk084/w-{d}/b5/p12/{d}/{SET}"
    _, frm = rd(f"{pdir}/forms.csv")
    syms = {r["symbol"] for r in frm}
    od = f"{OUT}/{d[:7]}/{d}/{SET}"
    os.makedirs(od, exist_ok=True)
    for kind in ("rounds", "signals"):
        head, rows = rd(f"{pdir}/{kind}.csv")
        cols = fields(f"{pdir}/{kind}.csv")
        rows = [r for r in rows if r["form"] in FORMS and r["form"] != B1 or False]
        # B1: основной слой TK-065 + досчёт ws- (перекрывает символы основного)
        main_h, main = rd(f"/data/tk065/w-{d}/b5/r2/{d}/{SET}/{kind}.csv")
        _, sup = rd(f"/data/tk065/ws-{d}/b5/r2/{d}/{SET}/{kind}.csv")
        sup_syms = {r["symbol"] for r in sup}
        b1 = [r for r in main if r["form"] == B1 and r["symbol"] not in sup_syms] + [r for r in sup if r["form"] == B1]
        b1 = [r for r in b1 if r["symbol"] in syms]
        with open(f"{od}/{kind}.csv", "w", encoding="utf-8", newline="") as fh:
            for h in head:
                fh.write(h + "\n")
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(b1 + rows)
print("ok")
