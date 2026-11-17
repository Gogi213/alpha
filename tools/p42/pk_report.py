"""П-42 §13.11: отчёт по опции движка `pk=1` — `b0pk` рядом с `b0`: $/сут. по половинам с интервалом, итог, сделки, вердикт §10 (m = 533 / 522),
парная разность по суткам и её чтение (правило §13.11), разрез сделок с флагом A (только 566, `features.jsonl`). Запуск на srv:

  P42_ROOT=/data/p42-charts/pkroot P42_FQ_COL=first_day_ge_12M python3 pk_report.py <544|566> [features.jsonl]

Каталоги: /data/p42-t<U>/out/pk/{b0,b0pk} (прогон `runpk.sh`) и /data/p42-t<U>/out/stage1/b0 (прежний b0, v11) — гейт «байт в байт».
"""
import csv
import filecmp
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import detstat  # noqa: E402
import report_tick  # noqa: E402
from lib import A, B, ND, SIDES  # noqa: E402
from report import fq_days  # noqa: E402

M = {"long": 533, "short": 522}
T0 = 1_788_220_800_000
TAU_BPS = 20.0


def daily(vdir, side):
    return np.array([float(x["pnl"]) for x in csv.DictReader(open(os.path.join(vdir, side + "-daily.csv")))])


def read_tr(vdir, side):
    return list(csv.DictReader(open(os.path.join(vdir, side + "-trades.csv"))))


def gate(new, old):
    """Побайтовое сравнение трейдов, суточных и по-монетных CSV двух прогонов."""
    out = {}
    for side in SIDES:
        for kind in ("trades", "daily", "coinday"):
            f = "%s-%s.csv" % (side, kind)
            out[f] = filecmp.cmp(os.path.join(new, f), os.path.join(old, f), shallow=False)
    return out


def read_rule(dm, dA, dB, d60, z):
    """Правило чтения Δ (§13.11 = §13.7): помогает / вредит / в шуме."""
    low_holm = d60["mean"] - z * d60["se"]
    if d60["mean"] > 0 and low_holm > 0 and dA["mean"] > 0 and dB["mean"] > 0:
        return "помогает"
    if d60["mean"] < 0 and d60["hi"] < 0:
        return "вредит"
    return "в шуме"


def main():
    U = sys.argv[1]
    feat_path = sys.argv[2] if len(sys.argv) > 2 else None
    root = "/data/p42-t%s/out" % U
    new, ref = os.path.join(root, "pk", "b0"), os.path.join(root, "stage1", "b0")
    P, D = {}, {s: {} for s in SIDES}
    for s in SIDES:
        for v, d in (("b0", new), ("b0pk", os.path.join(root, "pk", "b0pk"))):
            P[(s, v)] = d
            D[s][v] = daily(d, s)
    fq = fq_days()
    detstat.patch(report_tick)
    out = {"universe": U, "gate_vs_stage1_b0": gate(new, ref)}
    print("== вселенная %s; гейт pk=0 (v13) против прежнего b0 (v11): %s" % (
        U, "ВСЁ СОВПАДАЕТ" if all(out["gate_vs_stage1_b0"].values()) else "РАСХОЖДЕНИЕ " + str({k: v for k, v in out["gate_vs_stage1_b0"].items() if not v})))
    log = "/tmp/p42-t%s-pk.log" % U
    if os.path.exists(log):
        for ln in open(log, encoding="utf-8", errors="replace"):
            if ln.startswith("pk ("):
                out["pk_counters"] = ln.strip()
                print("  " + ln.strip())
    for side in SIDES:
        z = ND.inv_cdf(1 - 0.05 / (2 * M[side]))
        res = {"m": M[side], "z_holm": z, "cand": {}}
        for v in ("b0", "b0pk"):
            hs = report_tick.halves_stats(side, P, D, v, v, z, fq, None)
            res["cand"][v] = {"halves": hs, "verdict": report_tick.verdict(hs, z), "total": float(D[side][v].sum()),
                              "trades": hs["A"]["trades"] + hs["B"]["trades"]}
        d = D[side]["b0pk"] - D[side]["b0"]
        st = {"A": detstat.stat(d[A]), "B": detstat.stat(d[B]), "60": detstat.stat(d)}
        res["delta"] = {k: v for k, v in st.items()}
        res["delta_low_holm_60"] = st["60"]["mean"] - z * st["60"]["se"]
        res["delta_read"] = read_rule(None, st["A"], st["B"], st["60"], z)
        out[side] = res
        print("== %s: m=%d, z=%.3f" % (side, M[side], z))
        for v, c in res["cand"].items():
            a, b = c["halves"]["A"], c["halves"]["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d | B %+.2f [%.2f; %.2f] n=%d | итог 60 сут. %+.2f $, сделок %d | стопов %.0f%%/%.0f%% | нижняя после Холма A %.2f B %.2f" % (
                v, c["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], b["mean"], b["lo"], b["hi"], b["trades"], c["total"], c["trades"],
                a["stop_share"] * 100, b["stop_share"] * 100, a["low_holm"], b["low_holm"]))
            for k in ("no_best2_days", "no_best2_coins", "after_first_day", "no_worst2_days", "no_worst2_coins"):
                print("      %s: A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f]" % (
                    k, a[k]["mean"], a[k]["lo"], a[k]["hi"], b[k]["mean"], b[k]["lo"], b[k]["hi"]))
        print("  Δ(b0pk − b0): A %+.2f [%.2f; %.2f] | B %+.2f [%.2f; %.2f] | 60 сут. %+.2f [%.2f; %.2f], нижняя после Холма %.2f → %s" % (
            st["A"]["mean"], st["A"]["lo"], st["A"]["hi"], st["B"]["mean"], st["B"]["lo"], st["B"]["hi"],
            st["60"]["mean"], st["60"]["lo"], st["60"]["hi"], res["delta_low_holm_60"], res["delta_read"]))
    if feat_path and U == "566":
        feat = [json.loads(x) for x in open(feat_path, encoding="utf-8")]
        b0 = {s: read_tr(new, s) for s in SIDES}
        pk = {s: read_tr(os.path.join(root, "pk", "b0pk"), s) for s in SIDES}
        pk_keys = {s: {(r["sym"], r["entry_ms"]) for r in pk[s]} for s in SIDES}
        b0_keys = {s: {(r["sym"], r["entry_ms"]) for r in b0[s]} for s in SIDES}
        subsets = {
            "A (все)": lambda f: f["flag_A"],
            "A: прокол > τ": lambda f: f["flag_A"] and f["pierce_over_bps"] > TAU_BPS,
            "A: только закрытие минутки за уровнем": lambda f: f["flag_A"] and f["pierce_over_bps"] <= TAU_BPS,
            "A после рождения": lambda f: f["flag_A_post"],
            "A только до рождения": lambda f: f["flag_A"] and not f["flag_A_post"],
            "G": lambda f: f["flag_G"],
            "любой флаг": lambda f: f["n_flags"] > 0,
            "без флагов": lambda f: f["n_flags"] == 0,
            "все": lambda f: True,
        }
        flagged_idx = {s: {} for s in SIDES}
        for f in feat:
            flagged_idx[f["side"]][f["i"]] = f
        rows = {}
        for name, sel in subsets.items():
            for side in ("all",) + SIDES:
                n = gone = kept = 0
                usd_gone = usd_kept = 0.0
                for s in SIDES:
                    if side not in ("all", s):
                        continue
                    for i, r in enumerate(b0[s]):
                        f = flagged_idx[s].get(i)
                        if f is None or not sel(f):
                            continue
                        assert f["sym"] == r["sym"], (s, i, f["sym"], r["sym"])
                        n += 1
                        net = float(r["net"])
                        if (r["sym"], r["entry_ms"]) in pk_keys[s]:
                            kept += 1
                            usd_kept += net
                        else:
                            gone += 1
                            usd_gone += net
                rows[(name, side)] = dict(n=n, gone=gone, usd_gone=usd_gone, kept=kept, usd_kept=usd_kept)
        new_tr = {}
        for side in ("all",) + SIDES:
            n = 0
            usd = 0.0
            for s in SIDES:
                if side not in ("all", s):
                    continue
                for r in pk[s]:
                    if (r["sym"], r["entry_ms"]) not in b0_keys[s]:
                        n += 1
                        usd += float(r["net"])
            new_tr[side] = dict(n=n, usd=usd)
        out["flagA"] = {"%s|%s" % k: v for k, v in rows.items()}
        out["new_in_pk"] = new_tr
        print("== разрез сделок b0 (566, 4 мс) по флагам: исчезло из b0pk")
        for (name, side), v in rows.items():
            print("  %-42s %-5s: сделок %4d | исчезло %4d (%+.2f $) | осталось %4d (%+.2f $)" % (
                name, side, v["n"], v["gone"], v["usd_gone"], v["kept"], v["usd_kept"]))
        for side, v in new_tr.items():
            print("  новые в b0pk (нет в b0) %-5s: %d сделок, %+.2f $" % (side, v["n"], v["usd"]))
    json.dump(out, open(os.path.join(root, "pk", "report-pk.json"), "w"), ensure_ascii=False, indent=1,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
