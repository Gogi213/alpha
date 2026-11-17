"""П-42 §13.16: отчёт по входу «по ленте на момент сигнала + 4 мс» — `ow_e`, `b0pk_e` рядом с `ow`, `b0pk` (прежний вход): $/сут. по половинам с интервалом, итог,
сделки, сумма и среднее на сделку (медиану не пишем), доля в плюсе, вердикт §10 (m = 678 / 667), парная разность по суткам и её чтение (правило §13.11),
ворота «байт в байт» (ew = 0 против v17), судьба входов за уровнем при старом входе, появившиеся и исчезнувшие сделки.
Запуск на srv (566):

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 entry_report.py [<каталог прогона>]

Каталоги: /data/p42-t566/out/e16/{b0,b0pk,ow,ow_e,b0pk_e} (ew = 0 для первых трёх — контроль гейта), эталон гейта и «старый вход» —
/data/p42-t566/out/ow/{b0,b0pk,ow} (v17, 15.11).
"""
import csv
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

M = {"long": 678, "short": 667}
ROOT = "/data/p42-t566/out"
OLD = os.path.join(ROOT, "ow")
CELLS = ["b0", "b0pk", "ow", "b0pk_e", "ow_e"]
GATE_CELLS = ["b0", "b0pk", "ow"]
# (новая клетка, прежний вход, предзаписана); ow_e − ow — клетка-испытание, b0pk_e − b0pk — контроль, описательно
PAIRS = [("ow_e", "ow", True), ("b0pk_e", "b0pk", False)]
FATE = [("ow_e", "ow"), ("b0pk_e", "b0pk")]


def detail(vdir, side):
    p = os.path.join(vdir, side + "-detail.jsonl")
    return [json.loads(x) for x in open(p, encoding="utf-8")]


def beyond(r, side):
    """Цена входа уже за уровнем (определение 15.11): лонг — цена входа ≥ уровня, шорт — ≤ (цена входа с проскальзыванием)."""
    return r["entry_px_e8"] >= r["level_px_e8"] if side == "long" else r["entry_px_e8"] <= r["level_px_e8"]


def key(r, side):
    return (side, r["sym"], r["sig_ms"])


def rej(vdir, side):
    p = os.path.join(vdir, side + "-entryrej.csv")
    if not os.path.exists(p):
        return {}
    return {(side, x["sym"], int(x["sig_ms"])): x["why"] for x in csv.DictReader(open(p, encoding="utf-8"))}


def p90(v):
    s = sorted(v)
    return s[min(len(s) - 1, int(0.9 * len(s)))] if s else float("nan")


def mean(v):
    return float(np.mean(v)) if len(v) else float("nan")


def trade_stats(vdir, side):
    nets = [float(r["net"]) for r in wr.read_tr(vdir, side)]
    return {"n": len(nets), "sum": sum(nets), "mean": mean(nets), "pos_share": (sum(1 for x in nets if x > 0) / len(nets)) if nets else float("nan")}


def delay_stats(rows, a="entry_ms", b="sig_ms"):
    d = [r[a] - r[b] for r in rows]
    return {"n": len(d), "mean": mean(d), "p90": p90(d), "max": max(d) if d else None, "over_1s": sum(1 for x in d if x > 1000), "over_10s": sum(1 for x in d if x > 10000)}


def main():
    global ROOT, OLD
    R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "e16")
    cells = [c for c in CELLS if os.path.isdir(os.path.join(R, c))]
    out = {"cells": cells}
    fq = fq_days()
    detstat.patch(report_tick)
    g = {"%s_vs_v17" % c: wr.gate(os.path.join(R, c), os.path.join(OLD, c)) for c in GATE_CELLS if c in cells}
    out["gate"] = g
    nok = sum(1 for v in g.values() for ok in v.values() if ok)
    ntot = sum(len(v) for v in g.values())
    bad = {k: [f for f, ok in v.items() if not ok] for k, v in g.items()}
    print("== ворота (ew = 0 против v17): %d из %d файлов совпали; %s" % (nok, ntot, "ВСЁ СОВПАДАЕТ" if not any(bad.values()) else "РАСХОЖДЕНИЕ " + str({k: v for k, v in bad.items() if v})))
    meta = {c: json.load(open(os.path.join(R, c, "meta.json"))) for c in cells}
    P, D = {}, {s: {} for s in SIDES}
    for s in SIDES:
        for c in cells:
            P[(s, c)] = os.path.join(R, c)
            D[s][c] = wr.daily(P[(s, c)], s)
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        res = {"m": M[side], "z_holm": z, "cand": {}, "pairs": {}, "fate": {}}
        print("== %s: m=%d, z=%.3f" % (side, M[side], z))
        for c in cells:
            hs = report_tick.halves_stats(side, P, D, c, c, z, fq, None)
            ts = trade_stats(P[(side, c)], side)
            det = detail(P[(side, c)], side)
            bey = [r for r in det if beyond(r, side)]
            m = meta[c][side]
            res["cand"][c] = {"halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][c].sum()), "trades": ts, "signals_total": m.get("signals_total"),
                              "rej": m.get("rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]"), "entry_rej": m.get("entry_rej_[beyond,stale]"),
                              "beyond_n": len(bey), "beyond_usd": sum(r["net"] for r in bey), "delay_ms": delay_stats(det)}
            r = res["cand"][c]
            a, b = hs["A"], hs["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d | итог 60 сут. %+.2f $, сделок %d, сумма %+.2f $, среднее на сделку %+.3f $, в плюсе %.1f%% | стопов %.0f%%/%.0f%% | нижняя после Холма A %.2f B %.2f" % (
                c, r["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], b["mean"], b["lo"], b["hi"], b["trades"], r["total"], ts["n"], ts["sum"], ts["mean"],
                ts["pos_share"] * 100, a["stop_share"] * 100, b["stop_share"] * 100, a["low_holm"], b["low_holm"]))
            print("      сигналов %s; отказы [расходован, занята, пауза монеты, общая пауза, потолок P, нет исхода] %s; отказ входа [за уровнем, старая лента] %s; за уровнем: %d сделок (%+.2f $); задержка сигнал→вход мс: средняя %.0f, p90 %.0f, макс %s, > 1 с: %d, > 10 с: %d" % (
                r["signals_total"], r["rej"], r["entry_rej"], r["beyond_n"], r["beyond_usd"], r["delay_ms"]["mean"], r["delay_ms"]["p90"], r["delay_ms"]["max"], r["delay_ms"]["over_1s"], r["delay_ms"]["over_10s"]))
            for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
                print("      %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (k, a[k]["mean"], a[k]["lo"], a[k]["hi"], b[k]["mean"], b[k]["lo"], b[k]["hi"]))
        for new, base, prereg in PAIRS:
            if new not in cells or base not in cells:
                continue
            d = D[side][new] - D[side][base]
            st = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
            low = st["60"]["mean"] - z * st["60"]["se"]
            rd = wr.read_rule(st["A"], st["B"], st["60"], z)
            tn = {(r["sym"], r["sig_ms"]): float(r["net"]) for r in detail(P[(side, new)], side)}
            to = {(r["sym"], r["sig_ms"]): float(r["net"]) for r in detail(P[(side, base)], side)}
            gone = [k for k in to if k not in tn]
            newk = [k for k in tn if k not in to]
            pr = {"delta": st, "low_holm_60": low, "read": rd, "prereg": prereg, "gone": {"n": len(gone), "usd": sum(to[k] for k in gone)},
                  "new": {"n": len(newk), "usd": sum(tn[k] for k in newk)}, "kept_n": len(tn) - len(newk)}
            res["pairs"]["%s-%s" % (new, base)] = pr
            print("  Δ(%s − %s)%s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f] | 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s | исчезло %d (%+.2f $), появилось %d (%+.2f $), общих %d" % (
                new, base, "" if prereg else " (описательно)", st["A"]["mean"], st["A"]["lo"], st["A"]["hi"], st["B"]["mean"], st["B"]["lo"], st["B"]["hi"],
                st["60"]["mean"], st["60"]["lo"], st["60"]["hi"], low, rd, len(gone), pr["gone"]["usd"], len(newk), pr["new"]["usd"], pr["kept_n"]))
        # судьба входов за уровнем при старом входе
        for new, base in FATE:
            if new not in cells or base not in cells:
                continue
            old_det = detail(P[(side, base)], side)
            new_det = detail(P[(side, new)], side)
            nk = {key(r, side): r for r in new_det}
            rj = rej(P[(side, new)], side)
            old_bey = [r for r in old_det if beyond(r, side)]
            cls = {"rej_beyond": [], "rej_stale": [], "filled": [], "not_attempted": []}
            for r in old_bey:
                k = key(r, side)
                if k in nk:
                    cls["filled"].append((r, nk[k]))
                elif k in rj:
                    cls["rej_" + rj[k]].append((r, None))
                else:
                    cls["not_attempted"].append((r, None))
            fate = {c: {"n": len(v), "old_usd": sum(x[0]["net"] for x in v), "new_usd": sum(x[1]["net"] for x in v if x[1])} for c, v in cls.items()}
            fate["old_beyond_n"] = len(old_bey)
            fate["old_beyond_usd"] = sum(r["net"] for r in old_bey)
            res["fate"][new] = fate
            print("  судьба входов за уровнем при старом входе (%s → %s): всего %d (%+.2f $); " % (base, new, len(old_bey), fate["old_beyond_usd"]) + "; ".join(
                "%s %d (старые %+.2f $, новые %+.2f $)" % (c, fate[c]["n"], fate[c]["old_usd"], fate[c]["new_usd"]) for c in cls))
            # цена входа: новая против старой на тех же сигналах (положительно — новая хуже)
            dl = []
            for r in new_det:
                if "old_entry_px_e8" in r:
                    s_ = 1 if side == "long" else -1
                    dl.append(s_ * (r["entry_px_e8"] - r["old_entry_px_e8"]) / r["old_entry_px_e8"] * 1e4)
            if dl:
                od = [r["old_entry_ms"] - r["sig_ms"] for r in new_det if "old_entry_ms" in r]
                res["fate"][new]["entry_px_new_minus_old_bp"] = {"n": len(dl), "mean": mean(dl), "mean_abs": mean([abs(x) for x in dl]), "p90_abs": p90([abs(x) for x in dl])}
                res["fate"][new]["old_entry_delay_ms_on_new_signals"] = {"n": len(od), "mean": mean(od), "p90": p90(od), "max": max(od), "over_1s": sum(1 for x in od if x > 1000)}
                print("  цена входа новая − старая на сигналах %s (бп, + = новая хуже): среднее %+.2f, средний |Δ| %.2f, p90 |Δ| %.2f; прежний вход на этих сигналах: задержка средняя %.0f мс, p90 %.0f, макс %d, > 1 с: %d" % (
                    new, mean(dl), mean([abs(x) for x in dl]), p90([abs(x) for x in dl]), mean(od), p90(od), max(od), sum(1 for x in od if x > 1000)))
        out[side] = res
    json.dump(out, open(os.path.join(R, "report-e16.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
