"""П-42 §13.15: отчёт по клетке `ow` (pk=1 pb=1 + свежий уровень §13.14, база) рядом с `b0` и `b0pk`: $/сут. по половинам с интервалом, итог, сделки,
вердикт §10 (m = 677 / 666), парная разность по суткам и её чтение (правило §13.11), ворота «байт в байт», счётчики движка, разрез сделок.
Запуск на srv (566):

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 ow_report.py

Каталоги: /data/p42-t566/out/ow/{b0,b0pk,b0pb,fr_base,ow} (прогон `runfresh.sh 566 ow`), эталоны ворот: out/pk/{b0,b0pk} (v13), out/ws/b0pb (v15),
out/fr1/fr_base (v16, три CSV), out/fr1d/fr_base (v16, detail).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import report_tick  # noqa: E402
import ws_report as wr  # noqa: E402
from lib import A, B, ND, SIDES  # noqa: E402
from report import fq_days  # noqa: E402

M = {"long": 677, "short": 666}
ROOT = "/data/p42-t566/out"
OW = os.path.join(ROOT, "ow")
CELLS = ["b0", "b0pk", "ow", "b0pb", "fr_base"]
PAIRS = [("ow", "b0", True), ("ow", "b0pk", True), ("b0pb", "b0", False), ("fr_base", "b0", False), ("ow", "fr_base", False)]
CSV3 = ("trades", "daily", "coinday")


def main():
    cells = [c for c in CELLS if os.path.isdir(os.path.join(OW, c))]
    out = {"cells": cells}
    fq = fq_days()
    detstat.patch(report_tick)
    g = {"b0_vs_v13": wr.gate(os.path.join(OW, "b0"), os.path.join(ROOT, "pk", "b0")),
         "b0pk_vs_v13": wr.gate(os.path.join(OW, "b0pk"), os.path.join(ROOT, "pk", "b0pk")),
         "b0pb_vs_v15": wr.gate(os.path.join(OW, "b0pb"), os.path.join(ROOT, "ws", "b0pb")),
         "fr_base_vs_v16_csv": wr.gate(os.path.join(OW, "fr_base"), os.path.join(ROOT, "fr1", "fr_base"), CSV3),
         "fr_base_vs_v16_detail": wr.gate(os.path.join(OW, "fr_base"), os.path.join(ROOT, "fr1d", "fr_base"))}
    out["gate"] = g
    nok = sum(1 for v in g.values() for ok in v.values() if ok)
    ntot = sum(len(v) for v in g.values())
    bad = {k: [f for f, ok in v.items() if not ok] for k, v in g.items()}
    print("== ворота: %d из %d файлов совпали; %s" % (nok, ntot, "ВСЁ СОВПАДАЕТ" if not any(bad.values()) else "РАСХОЖДЕНИЕ " + str({k: v for k, v in bad.items() if v})))
    log = "/tmp/p42-t566-ow.log"
    out["log_counters"] = []
    if os.path.exists(log):
        for ln in open(log, encoding="utf-8", errors="replace"):
            if ln.startswith(("pk (", "pb (", "свежий")):
                out["log_counters"].append(ln.strip())
                print("  " + ln.strip())
    meta = {c: json.load(open(os.path.join(OW, c, "meta.json"))) for c in cells}
    P, D = {}, {s: {} for s in SIDES}
    for s in SIDES:
        for c in cells:
            P[(s, c)] = os.path.join(OW, c)
            D[s][c] = wr.daily(P[(s, c)], s)
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        res = {"m": M[side], "z_holm": z, "cand": {}, "pairs": {}}
        print("== %s: m=%d, z=%.3f" % (side, M[side], z))
        for c in cells:
            hs = report_tick.halves_stats(side, P, D, c, c, z, fq, None)
            m = meta[c][side]
            res["cand"][c] = {"halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][c].sum()),
                              "trades": hs["A"]["trades"] + hs["B"]["trades"], "signals_total": m.get("signals_total")}
            r = res["cand"][c]
            a, b = hs["A"], hs["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d | итог 60 сут. %+.2f $, сделок %d | стопов %.0f%%/%.0f%% | нижняя после Холма A %.2f B %.2f | сигналов %s" % (
                c, r["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], b["mean"], b["lo"], b["hi"], b["trades"], r["total"], r["trades"],
                a["stop_share"] * 100, b["stop_share"] * 100, a["low_holm"], b["low_holm"], r["signals_total"]))
            for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
                print("      %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (
                    k, a[k]["mean"], a[k]["lo"], a[k]["hi"], b[k]["mean"], b[k]["lo"], b[k]["hi"]))
        for new, base, prereg in PAIRS:
            if new not in cells or base not in cells:
                continue
            d = D[side][new] - D[side][base]
            st = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
            low = st["60"]["mean"] - z * st["60"]["se"]
            rd = wr.read_rule(st["A"], st["B"], st["60"], z)
            tn, tb = wr.keyset(wr.read_tr(P[(side, new)], side)), wr.keyset(wr.read_tr(P[(side, base)], side))
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
    # контроль: L0005_INTWUSDT (лонг i=4 набора b0pk) и S0226_MINIMAXUSDT (шорт, entry_ms 3 116 287 000 от 01.09) в `ow` отсутствуют
    chk = {}
    for tag, side, sym, ems in (("L0005_INTWUSDT", "long", "INTWUSDT", None), ("S0226_MINIMAXUSDT", "short", "MINIMAXUSDT", 3116287000)):
        row = {}
        for c in ("b0", "b0pk", "ow"):
            rows = wr.read_tr(P[(side, c)], side)
            if ems is None:  # L0005: пятая сделка лонга набора b0pk — по ней берём время входа
                ref = wr.read_tr(P[(side, "b0pk")], side)[4]
                assert ref["sym"] == sym, ref["sym"]
                ems = int(ref["entry_ms"])
                win = (ems, ems + 1)
            else:  # время входа в задании — с точностью до секунды
                win = (ems, ems + 1000)
            row[c] = sum(1 for r in rows if r["sym"] == sym and win[0] <= int(r["entry_ms"]) < win[1])
        chk[tag] = row
        print("  контроль %s: сделок в b0 / b0pk / ow = %d / %d / %d" % (tag, row["b0"], row["b0pk"], row["ow"]))
    out["control"] = chk
    json.dump(out, open(os.path.join(OW, "report-ow.json"), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
