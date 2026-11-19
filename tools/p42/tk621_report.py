#!/usr/bin/env python3
"""TK-621: per-month table of L_base / S_base for the universes run by tk621_run.py (sum, mean per trade, count, % of margin at leverage 2,
max drawdown, loss streak, stops per day). Windows spliced as retitr_full.splice (entry day inside the window's credited range).
Only reads <root>/out/<tag>/. P&L of a trade = gross - fees (no funding), $500 position, margin at leverage 2 = $250.
  python3 tk621_report.py <root> <contracts.csv> <tag> [<tag> ...] [--json out.json]"""
import csv
import datetime as D
import json
import os
import sys
from collections import Counter

NOTIONAL, MARGIN = 500.0, 250.0
DAY_MS = 86_400_000
WIN = (("wa", "2026-06-17", "2026-07-01", "2026-08-25"), ("wb", "2026-07-25", "2026-08-26", "2026-10-02"), ("wc", "2026-09-01", "2026-10-03", "2026-11-13"))
MONTHS = (("jul", "2026-07-01", "2026-07-31"), ("aug", "2026-08-01", "2026-08-31"), ("sep", "2026-09-01", "2026-09-30"),
          ("oct", "2026-10-01", "2026-10-31"), ("nov", "2026-11-01", "2026-11-13"), ("all", "2026-07-01", "2026-11-13"))
d = D.date.fromisoformat


def load(root, tag, contracts):
    """{side: [trade dict]} spliced over the windows."""
    out = {"long": [], "short": []}
    for w, t0, lo, hi in WIN:
        base = f"{root}/out/{tag}/{w}"
        for v in sorted(os.listdir(base)):
            if not os.path.isfile(f"{base}/{v}/meta.json"):
                continue
            meta = json.load(open(f"{base}/{v}/meta.json"))
            side = "short" if "tx_min: 20" in meta["params"] else "long"     # S_base has tt=20, L_base has none
            t0d = d(t0)
            for r in csv.DictReader(open(f"{base}/{v}/{side}-trades.csv")):
                day = t0d + D.timedelta(days=int(r["entry_ms"]) // DAY_MS)
                if d(lo) <= day <= d(hi):
                    ems = int(D.datetime(t0d.year, t0d.month, t0d.day, tzinfo=D.timezone.utc).timestamp() * 1000)
                    out[side].append({"sym": r["sym"], "day": day, "e": ems + int(r["entry_ms"]), "x": ems + int(r["exit_ms"]),
                                      "v": float(r["gross"]) - float(r["fees"]), "kind": r["kind"], "tradfi": contracts.get(r["sym"], {}).get("tradfi", 0)})
    for s in out:
        out[s].sort(key=lambda r: r["e"])
    return out


def metrics(rows, lo, hi):
    rs = [r for r in rows if d(lo) <= r["day"] <= d(hi)]
    ndays = (d(hi) - d(lo)).days + 1
    n = len(rs)
    S = sum(r["v"] for r in rs)
    ro = sorted(rs, key=lambda r: r["x"])
    eq = pk = dd = 0.0
    cur = mx = 0
    run = worst = 0.0
    for r in ro:
        eq += r["v"]
        pk = max(pk, eq)
        dd = min(dd, eq - pk)
        if r["v"] < 0:
            cur += 1
            run += r["v"]
            mx, worst = max(mx, cur), min(worst, run)
        else:
            cur, run = 0, 0.0
    stops = [r for r in rs if r["kind"] == "stop"]
    return {"n": n, "sum": round(S, 2), "mean": round(S / n, 3) if n else 0.0, "sum_pct_margin": round(S / MARGIN * 100, 1),
            "mean_pct_margin": round(S / n / MARGIN * 100, 3) if n else 0.0, "maxdd": round(dd, 2), "maxdd_pct_margin": round(dd / MARGIN * 100, 1),
            "loss_streak": mx, "loss_streak_sum": round(worst, 2), "stops": len(stops), "stops_sum": round(sum(r["v"] for r in stops), 2),
            "stops_per_day": round(len(stops) / ndays, 3), "days": ndays, "coins": len({r["sym"] for r in rs}),
            "tradfi_n": sum(r["tradfi"] for r in rs), "tradfi_sum": round(sum(r["v"] for r in rs if r["tradfi"]), 2),
            "forced": sum(1 for r in rs if r["kind"] == "forced"), "kinds": dict(Counter(r["kind"] for r in rs))}


def main():
    args = sys.argv[1:]
    js = None
    if "--json" in args:
        i = args.index("--json")
        js = args[i + 1]
        del args[i:i + 2]
    root, cfile, tags = args[0], args[1], args[2:]
    contracts = {r["symbol"]: {"tradfi": int(r["tradfi"])} for r in csv.DictReader(open(cfile))}
    res = {}
    for tag in tags:
        tr = load(root, tag, contracts)
        res[tag] = {side: {m: metrics(tr[side], lo, hi) for m, lo, hi in MONTHS} for side in tr}
        for side in ("long", "short"):
            print(f"\n== {tag} {side}  (n, sum $, mean $/trade, sum %margin, maxDD $ / %margin, loss streak / sum, stops n/day, coins)")
            for m, _, _ in MONTHS:
                x = res[tag][side][m]
                print(f"{m:>4} n={x['n']:4d} S={x['sum']:9.2f} mean={x['mean']:7.3f} S%={x['sum_pct_margin']:8.1f} dd={x['maxdd']:8.2f}/{x['maxdd_pct_margin']:6.1f}% "
                      f"streak={x['loss_streak']:2d}/{x['loss_streak_sum']:7.2f} stops={x['stops']:3d}/{x['stops_per_day']:.3f}/d coins={x['coins']:3d} tradfi n={x['tradfi_n']} S={x['tradfi_sum']} forced={x['forced']}")
    if js:
        json.dump(res, open(js, "w"), indent=1, default=str)


main()
