#!/usr/bin/env python3
"""TK-356 → Летопись минусов (G-012): находки шага цены (tick_bps) из готовых сделок; идемпотентно (по id). Только groupby, без бутстрапа.
Использование: tk356-lf.py TURNOVER.csv TURNOVER-P24.csv P24-TRADES.csv"""
import sys, json, numpy as np, pandas as pd
F = "docs/findings/"; R = "docs/registry/loss_findings.jsonl"
tv = pd.read_csv(sys.argv[1]); tv = tv[tv.err.isna()]
fut = pd.concat([pd.read_csv(F + "tk327-features-fut-2026-10-20.csv"), pd.read_csv(F + "p23-fut-B-features-2026-10-20.csv")])
d = fut
tp = pd.read_csv(sys.argv[2]); tp = tp[tp.err.isna()]; tv = pd.concat([tv, tp]).drop_duplicates(["mode", "sym", "day"])
t = pd.read_csv(sys.argv[3]).rename(columns={"symbol": "sym", "day_utc": "day"}); t["mon"] = t.day.str[5:7].astype(int)
for f in ("pct0.1", "pct0.5"):
    x = t[t.form.str.startswith(f + "-")].copy(); x["mode"] = "p24-" + f; d = pd.concat([d, x[["mode", "sym", "day", "mon", "entry_px", "net_bps"]]])
p = tv[tv["mode"] == "fut"].copy()
for f in ("p24-pct0.1", "p24-pct0.5"): q = p.copy(); q["mode"] = f; tv = pd.concat([tv, q])
d = d.merge(tv[["mode", "sym", "day", "tick_bin"]], on=["mode", "sym", "day"], how="left")
d["tick_bps"] = d.tick_bin / d.entry_px * 1e4; d["usd"] = d.net_bps * 500 / 1e4
want = {"fut": ["Q2", "Q5"], "p24-pct0.1": ["Q4", "Q5"], "p24-pct0.5": ["Q4", "Q5"]}
src = {"fut": "docs/findings/tk327-features-fut-2026-10-20.csv + p23-fut-B-features (B1 fut, полная база A+B)", "p24-pct0.1": "TK-344 rounds.csv, pct0.1 (data/p24-trades.csv)", "p24-pct0.5": "TK-344 rounds.csv, pct0.5 (data/p24-trades.csv)"}
new = []
for mode, qs in want.items():
    m = d[d["mode"] == mode].dropna(subset=["tick_bps"]).copy()
    m["q"] = pd.qcut(m.tick_bps.rank(method="first"), 5, labels=[f"Q{i}" for i in range(1, 6)])
    tot = m.usd.sum(); negtot = m.groupby("q", observed=True).usd.sum().clip(upper=0).sum()
    for q in qs:
        g = m[m.q == q]; mo = g.groupby("mon").usd.agg(["size", "sum"])
        s = float(g.usd.sum()); base = tot if tot < 0 else negtot
        pl = 100 * s / base if s < 0 and base < 0 else 0.0
        new.append({"id": f"LF-{mode}-tick_bps-{q}", "batch": "TK-356", "market": "fut", "run_id": None, "cell_id": None, "feature": "tick_bps", "group": q,
            "bounds": [round(float(g.tick_bps.min()), 4), round(float(g.tick_bps.max()), 4)], "n": int(len(g)), "pct_trades": round(100 * len(g) / len(m), 2),
            "sum_usd": round(s, 2), "pct_loss": round(pl, 2), "lift": round(pl / (100 * len(g) / len(m)), 3),
            "months_neg": int((mo["sum"] < 0).sum()), "months_n": int(len(mo)),
            "months": [[(k if isinstance(k, str) else f"2026-{int(k):02d}"), int(r["size"]), round(float(r["sum"]), 2)] for k, r in mo.iterrows()],
            "metric": "a", "tariff": "фьюч-модель (а): net_bps как есть", "pct_loss_base": "Σ группы / Σ всех (итог набора −)" if tot < 0 else "Σ группы / Σ групп с Σ<0 (итог набора +)",
            "raw": "docs/findings/tk356-turnover-tick-2026-10-22.csv", "src": src[mode], "report": "docs/findings/tk356-turnover-tick-2026-10-22.md",
            "n_trials": 8, "status": "наблюдение", "note": "кандидат, не правило: срез выбран по тем же данным; проверка — сито/титровка вне подбора"})
have = {json.loads(l)["id"] for l in open(R, encoding="utf-8") if l.strip()}
add = [r for r in new if r["id"] not in have]
with open(R, "a", encoding="utf-8", newline="\n") as f:
    for r in add: f.write(json.dumps(r, ensure_ascii=False) + "\n")
for r in new: print(r["id"], r["n"], r["sum_usd"], f'{r["months_neg"]}/{r["months_n"]}', "new" if r in add else "exists")
