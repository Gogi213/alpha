#!/usr/bin/env python3
"""TK-303: план F2 П-15 — master.plan(P-15, 2) по сито-CSV TK-298 (колонка «итог»), состав и урезки Судьи (reviews/tk302-f2-plan).
Г-102 A (внутри F1; за край T — Г-102 B — не подаётся), Г-146, Г-134, Г-72 две зоны (зона 16 с/q33 вне F2 по потолку 100), Г-108.
  python tools/compute/tk303-f2-plan.py [<out.jsonl>]   (из корня репозитория)"""
import csv, json, os, sys
sys.path.insert(0, os.path.join("tools", "registry"))
import master

CSV = os.path.join("docs", "findings", "tk298-p15-f1-sieve-2026-10-18.csv")
HYPS = ["Г-102", "Г-146", "Г-134", "Г-72", "Г-108"]
CUT = {"Г-72": {"btc_lag_s": "16", "btc_thr_q": "33"}}   # зона вне F2 (Судья, tk302: потолок 100, две зоны по min p)


def f1():
    by = {}
    for r in csv.DictReader(open(CSV, encoding="utf-8-sig")):
        s = r["итог"]
        defd = not s.startswith("не определена")
        ax = json.loads(r["axes"])
        good = s.startswith("годна") and not all(str(ax.get(k)) == v for k, v in CUT.get(r["hyp"], {"": None}).items())
        by.setdefault(r["hyp"], []).append({"ax": ax, "good": good, "defd": defd,
                                            "key": (float(r["p"]), -float(r["dSR"]), -int(r["days_A"])) if defd else None})
    return by


if __name__ == "__main__":
    master.plan("P-15", 2, HYPS, sys.argv[1] if len(sys.argv) > 1 else None, f1=f1())
