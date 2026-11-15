"""П-42 §13.13: отчёт по опциям `ap` / `pb` — `b0ws`, `b0wspk` рядом с `b0`, `b0pk`: $/сут. по половинам с интервалом, итог, сделки, вердикт §10 (m = 592 / 581),
парная разность по суткам и её чтение (правило §13.11), разложение (`b0ap`, `b0pb`), оценка потерянных входов подхода (`b0ws_pool0`), счётчики движка.
Запуск на srv:

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 ws_report.py <544|566>

Каталоги: /data/p42-t<U>/out/ws/<вариант> (прогон `runws.sh`) и /data/p42-t<U>/out/pk/{b0,b0pk} (v13) — гейт «байт в байт».
"""
import csv
import filecmp
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import report_tick  # noqa: E402
from lib import A, B, ND, SIDES  # noqa: E402
from report import fq_days  # noqa: E402

M = {"long": 592, "short": 581}
CELLS = ["b0", "b0pk", "b0ws", "b0wspk", "b0ap", "b0pb", "b0ws_pool0"]
# (новая клетка, база); первые две — предзаписанные (чтение по §13.11), остальные описательно
PAIRS = [("b0ws", "b0", True), ("b0wspk", "b0pk", True), ("b0wspk", "b0", False), ("b0ap", "b0", False), ("b0pb", "b0", False),
         ("b0ws_pool0", "b0ws", False)]
KINDS = ("trades", "daily", "coinday", "detail")


def fname(side, kind):
    return "%s-%s.%s" % (side, kind, "jsonl" if kind == "detail" else "csv")


def daily(vdir, side):
    return np.array([float(x["pnl"]) for x in csv.DictReader(open(os.path.join(vdir, side + "-daily.csv")))])


def read_tr(vdir, side):
    return list(csv.DictReader(open(os.path.join(vdir, side + "-trades.csv"))))


def gate(new, old, kinds=KINDS):
    out = {}
    for side in SIDES:
        for kind in kinds:
            f = fname(side, kind)
            out[f] = filecmp.cmp(os.path.join(new, f), os.path.join(old, f), shallow=False)
    return out


def read_rule(dA, dB, d60, z):
    """Правило чтения Δ (§13.11 = §13.7): помогает / вредит / в шуме."""
    low_holm = d60["mean"] - z * d60["se"]
    if d60["mean"] > 0 and low_holm > 0 and dA["mean"] > 0 and dB["mean"] > 0:
        return "помогает"
    if d60["mean"] < 0 and d60["hi"] < 0:
        return "вредит"
    return "в шуме"


def keyset(tr):
    return {(r["sym"], r["entry_ms"]): float(r["net"]) for r in tr}


def main():
    U = sys.argv[1]
    root = "/data/p42-t%s/out" % U
    ws = os.path.join(root, "ws")
    cells = [c for c in CELLS if os.path.isdir(os.path.join(ws, c))]
    out = {"universe": U, "cells": cells}
    fq = fq_days()
    detstat.patch(report_tick)
    # ворота: ap = pb = 0 побайтово как v13; ap = 2 — три CSV как b0
    g = {"b0_vs_v13": gate(os.path.join(ws, "b0"), os.path.join(root, "pk", "b0")),
         "b0pk_vs_v13": gate(os.path.join(ws, "b0pk"), os.path.join(root, "pk", "b0pk")),
         "b0lab_vs_b0": gate(os.path.join(ws, "b0lab"), os.path.join(ws, "b0"), ("trades", "daily", "coinday"))}
    out["gate"] = g
    bad = {k: [f for f, ok in v.items() if not ok] for k, v in g.items()}
    print("== вселенная %s; ворота: %s" % (U, "ВСЁ СОВПАДАЕТ" if not any(bad.values()) else "РАСХОЖДЕНИЕ " + str({k: v for k, v in bad.items() if v})))
    log = "/tmp/p42-t%s-ws.log" % U
    out["log_counters"] = []
    if os.path.exists(log):
        for ln in open(log, encoding="utf-8", errors="replace"):
            if ln.startswith("pb (") or ln.startswith("ap (") or "ap_wrong" in ln:
                out["log_counters"].append(ln.strip())
                print("  " + ln.strip())
    meta = {}
    for c in cells:
        meta[c] = json.load(open(os.path.join(ws, c, "meta.json")))
    P, D = {}, {s: {} for s in SIDES}
    for s in SIDES:
        for c in cells:
            P[(s, c)] = os.path.join(ws, c)
            D[s][c] = daily(P[(s, c)], s)
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        res = {"m": M[side], "z_holm": z, "cand": {}, "pairs": {}}
        print("== %s: m=%d, z=%.3f" % (side, M[side], z))
        for c in cells:
            hs = report_tick.halves_stats(side, P, D, c, c, z, fq, None)
            m = meta[c][side]
            res["cand"][c] = {"halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][c].sum()),
                              "trades": hs["A"]["trades"] + hs["B"]["trades"], "signals_total": m.get("signals_total"),
                              "signals_AB": m.get("signals_AB"), "ap_wrong_signals": m.get("ap_wrong_signals")}
            r = res["cand"][c]
            a, b = hs["A"], hs["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d | итог 60 сут. %+.2f $, сделок %d | стопов %.0f%%/%.0f%% | нижняя после Холма A %.2f B %.2f | сигналов %s, ap-отвергнуто %s" % (
                c, r["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], b["mean"], b["lo"], b["hi"], b["trades"], r["total"], r["trades"],
                a["stop_share"] * 100, b["stop_share"] * 100, a["low_holm"], b["low_holm"], r["signals_total"], r["ap_wrong_signals"]))
            for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
                print("      %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (
                    k, a[k]["mean"], a[k]["lo"], a[k]["hi"], b[k]["mean"], b[k]["lo"], b[k]["hi"]))
        for new, base, prereg in PAIRS:
            if new not in cells or base not in cells:
                continue
            d = D[side][new] - D[side][base]
            st = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
            low = st["60"]["mean"] - z * st["60"]["se"]
            rd = read_rule(st["A"], st["B"], st["60"], z)
            tn, tb = keyset(read_tr(P[(side, new)], side)), keyset(read_tr(P[(side, base)], side))
            gone = [k for k in tb if k not in tn]
            newk = [k for k in tn if k not in tb]
            pr = {"delta": st, "low_holm_60": low, "read": rd, "prereg": prereg,
                  "gone": {"n": len(gone), "usd": sum(tb[k] for k in gone)}, "new": {"n": len(newk), "usd": sum(tn[k] for k in newk)},
                  "kept_n": len(tn) - len(newk)}
            res["pairs"]["%s-%s" % (new, base)] = pr
            print("  Δ(%s − %s)%s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f] | 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s | исчезло %d (%+.2f $), новых %d (%+.2f $)" % (
                new, base, "" if prereg else " (описательно)", st["A"]["mean"], st["A"]["lo"], st["A"]["hi"], st["B"]["mean"], st["B"]["lo"], st["B"]["hi"],
                st["60"]["mean"], st["60"]["lo"], st["60"]["hi"], low, rd, len(gone), pr["gone"]["usd"], len(newk), pr["new"]["usd"]))
        out[side] = res
    json.dump(out, open(os.path.join(ws, "report-ws.json"), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
