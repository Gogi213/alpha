#!/usr/bin/env python3
"""TK-274 (П-15 §0 п. 3, §3): клетки F1 без месячных порогов — Г-108 (49) и Г-102 (42) — с каноническими хешами и строками
--cells; сверка с routes.json формы (прокси Летописи; полная сверка — `have_hashes` на calc).
Остальные гипотезы (Г-72, Г-146, Г-134) зависят от файла порогов — в выход попадают пустыми (pending), хеши — после master.
    python tools/registry/p15_scope.py [--out docs/findings/tk274-p15-cells-2026-10-16.json]"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch  # noqa: E402
import cellhash_reg as cr  # noqa: E402

BASE = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"  # B1 (П-15 вводная)
SET = "t-bid-btc4h-q1"
G108 = (0, 1, 2, 4, 8, 16, 32)  # зазор, bps
W108 = (5, 15, 30, 60, 300, 900, 1800)  # окно, с
K102 = (1, 2, 3, 4, 6, 8)  # тики (верх — лимит каталога 8)
T102 = (5, 15, 30, 60, 120, 240, 480)  # полураспад, мин
ROUTES = os.path.join(HERE, "..", "launch", "routes.json")


def cfg_of(form):
    return {"form": form, "set": cr.B1_SET, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": cr.UNIVERSE["v171c"]}


def cells():
    out = {"Г-108": [], "Г-102": []}
    for g in G108:
        for w in W108:
            f = f"{BASE}-lvlback{g}w{w}"
            out["Г-108"].append({"axes": {"gap_bps": g, "secs": w}, "form": f, "line": f"{f} {SET}", "hash": ch.cell_hash(cfg_of(f))})
    for k in K102:
        for t in T102:
            f = f"retreat{k}t{t}-pct2-tr1x1-14400-ttl1800"
            out["Г-102"].append({"axes": {"k": k, "half_min": t}, "form": f, "line": f"{f} {SET}", "hash": ch.cell_hash(cfg_of(f))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "docs", "findings", "tk274-p15-cells-2026-10-16.json"))
    a = ap.parse_args()
    cs = cells()
    known = json.load(open(ROUTES, encoding="utf-8"))["lines"]
    res = {"protocol": "П-15 r2", "note": "Г-108/Г-102 — полные; Г-72/Г-146/Г-134 ждут файл порогов (pending)",
           "base_hash": ch.cell_hash(cfg_of(BASE)), "families": cs, "pending": ["Г-72", "Г-146", "Г-134"]}
    for hyp, rows in cs.items():
        hs = [r["hash"] for r in rows]
        res[f"check_{hyp}"] = {"cells": len(rows), "unique_hashes": len(set(hs)), "already_in_routes": sum(h in known for h in hs)}
    json.dump(res, open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k.startswith("check_")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
