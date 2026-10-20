#!/usr/bin/env python3
"""TK-274 (П-15 §0 п. 3, §3): клетки F1 без месячных порогов — Г-108 (49) и Г-102 (42) — с каноническими хешами и строками
--cells; сверка с routes.json формы (прокси Летописи; полная сверка — `have_hashes` на calc).
Г-72 и Г-146 — по файлам порогов (`--lag-thr` Г-72 от tools/analyze/btc-lag-thresholds.py, btc-thresholds-2026-10-11.json окно 24 ч):
хеш варианта «семья g72/g146 + оси», как Г-10 в master.py; совпавшие по всем 9 месяцам границы — одна клетка (dup_of).
Г-134 (R1) — pending: пороги по касаниям B1 считает конвейер R1.
    python tools/registry/p15_scope.py --lag-thr <btc-lag-thresholds.json> [--write-research]  # P-15-scope.json + P-15-thresholds.csv"""
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


Q9 = (5, 10, 20, 33, 50, 67, 80, 90, 95)
LAGS = (1, 2, 4, 8, 16, 32, 64, 118)  # с
LAGS_F2 = LAGS + (78, 91, 104)  # П-15 F2 (TK-303): окрестность зоны 118 с
Q_ALL = tuple(range(1, 100))  # F2: разрешимость 1 (П-14 §56); строки порогов — для всех целых q, клетки families — по Q9 (F1)
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]  # окно янв–сен (В-211); октябрь — вне
FIND = os.path.join(HERE, "..", "..", "docs", "findings")
RES = os.path.join(HERE, "..", "..", "docs", "research")


def variant_hash(fam, axes):
    cfg = cfg_of(BASE)
    cfg["variant"] = {"family": fam, "axes": {k: ch.num(v) for k, v in axes.items()}}
    return ch.cell_hash(cfg)


def threshold_cells(lag_thr, btc_thr):
    """Г-72 (8 лагов × 9 q) и Г-146 (btc24h × 9 q): клетки + строки порогов по месяцам; дубль = все 9 границ равны."""
    rows, fam72, fam146 = [], [], []
    for s in LAGS:
        seen = {}
        for q in Q9:
            ts = [lag_thr[m][str(s)][str(q)] for m in MONTHS]
            c = {"axes": {"btc_lag_s": s, "btc_thr_q": q}, "hash": variant_hash("g72", {"btc_lag_s": s, "btc_thr_q": q}), "thr": ts}
            if tuple(ts) in seen:
                c["dup_of"] = seen[tuple(ts)]
            else:
                seen[tuple(ts)] = q
            fam72.append(c)
    for s in LAGS_F2:   # строки порогов Г-72: все лаги × все целые q, если они есть в файле порогов (--all-q); иначе только то, что есть
        for q in Q_ALL:
            if all(str(q) in lag_thr[m].get(str(s), {}) for m in MONTHS):
                ts = [lag_thr[m][str(s)][str(q)] for m in MONTHS]
                rows += [("Г-72", f"btc_lag_{s}s", q, m, t, f"age=2700,side=bid,btc_lag_{s}s_min={t},btc4h_max=-44.55") for m, t in zip(MONTHS, ts)]
    for q in Q_ALL:
        if all(str(q) in btc_thr[m]["24"] for m in MONTHS):
            ts = [btc_thr[m]["24"][str(q)] for m in MONTHS]
            if q in Q9:
                fam146.append({"axes": {"btc_thr_q": q}, "hash": variant_hash("g146", {"btc_thr_q": q}), "thr": ts})
            rows += [("Г-146", "btc24h", q, m, t, f"age=2700,side=bid,btc4h_max=-44.55,btc24h_min={t}") for m, t in zip(MONTHS, ts)]
    return {"Г-72": fam72, "Г-146": fam146}, rows


def split_ab():
    import hashlib
    sp = json.load(open(os.path.join(FIND, "p13-split-2026-10-09.json"), encoding="utf-8"))
    pool = sorted(sp["A"] + sp["B"])
    is_a = lambda x: int(hashlib.sha256(x.encode()).hexdigest(), 16) % 2 == 0  # noqa: E731
    a, b = [x for x in pool if is_a(x)], [x for x in pool if not is_a(x)]
    assert a == sorted(sp["A"]) and b == sorted(sp["B"]), "разбиение А/Б разошлось с p13-split"
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(FIND, "tk274-p15-cells-2026-10-16.json"))
    ap.add_argument("--lag-thr", help="btc-lag-thresholds.json (Г-72); без него Г-72/Г-146 pending")
    ap.add_argument("--btc-thr", default=os.path.join(FIND, "btc-thresholds-2026-10-11.json"), help="btc-thresholds.json (Г-146, окно 24 ч); F2 — файл с --all-q")
    ap.add_argument("--write-research", action="store_true", help="записать docs/research/P-15-scope.json и P-15-thresholds.csv")
    a = ap.parse_args()
    cs = cells()
    known = json.load(open(ROUTES, encoding="utf-8"))["lines"]
    res = {"protocol": "П-15 r2", "note": "Г-134 ждёт пороги конвейера R1 (pending)", "base_hash": ch.cell_hash(cfg_of(BASE)), "families": cs,
           "pending": ["Г-134"]}
    rows = []
    if a.lag_thr:
        tc, rows = threshold_cells(json.load(open(a.lag_thr, encoding="utf-8")),
                                   json.load(open(a.btc_thr, encoding="utf-8")))
        res["families"].update(tc)
    else:
        res["pending"] = ["Г-72", "Г-146", "Г-134"]
    for hyp, fam in res["families"].items():
        hs = [r["hash"] for r in fam]
        res[f"check_{hyp}"] = {"cells": len(fam), "unique_hashes": len(set(hs)), "unique_cells": sum("dup_of" not in r for r in fam),
                               "already_in_routes": sum(h in known for h in hs)}
    json.dump(res, open(a.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k.startswith("check_")}, ensure_ascii=False))
    if a.write_research and rows:
        A, B = split_ab()
        scope = {"protocol": "П-15 r2", "m": 5, "m_note": "Г-146 — отдельная семья (Г-10 заменяет ядро btc4h окном и берёт _max; Г-146 оставляет btc4h и добавляет btc24h_min, §3а)",
                 "seed_g03": [1, 2, 3, 4, 5], "test_months": [4, 5, 6, 7, 8, 9], "window": "2026-01-01..2026-09-30", "boot": 20000,
                 "split": {"rule": "sha256(symbol) hex как целое, чётное -> A, нечётное -> B (П-13 §6 п. 1)", "pool": "v171c", "n_A": len(A), "n_B": len(B), "A": A, "B": B},
                 "base": {"form": BASE, "set": SET, "hash": res["base_hash"]},
                 "hash_rule": "Г-102/Г-108 — хеш формы (канон Летописи); Г-72/Г-146 — хеш варианта (семья g72/g146 + оси), как Г-10; dup_of — совпали все 9 границ",
                 "pending": res["pending"], "families": res["families"]}
        json.dump(scope, open(os.path.join(RES, "P-15-scope.json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
        with open(os.path.join(RES, "P-15-thresholds.csv"), "w", encoding="utf-8", newline="\n") as f:
            f.write("hyp;axis;q;month;threshold_bps;set_arg\n")
            for r in rows:
                f.write(";".join(str(x) for x in r) + "\n")


if __name__ == "__main__":
    main()
