"""П-42 шаг B (§13.2): числа по кандидатам на сделках → <ROOT>/report-tick.json и печать.

  P42_ROOT=data/p42v2 P42_FQ_COL=first_day_ge_12M P42_PRIOR_TRIALS='{"long":98,"short":93}' python tools/p42/report_tick.py <имя каталога tick-...> [--base-only]
Кандидаты стороны: тик-база (b0); перекрёстный (cB_ на сутках A, cA_ на сутках B — те же значения, что шаг A); «по букве» (uB_/uA_), если отличается;
топ-3 шага A (top3.json). Холм — по накопленному счётчику: шаг A (report.json: m_package) + испытания шага B (база 1 + перекрёстный 2 + топ-3).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from lib import A, B, HALVES, ND, ROOT, SIDES, drawdown_kpi, load_all, read_coinday, read_trades, stat  # noqa: E402
from phase import rng  # noqa: E402
from report import fq_days  # noqa: E402


def halves_stats(side, P, D, vA, vB, z_h, fq, r):
    out = {}
    for h, v in (("A", vA), ("B", vB)):
        sl = HALVES[h]
        x = D[side][v][sl]
        st = stat(x, r)
        st["low_holm"] = st["mean"] - z_h * st["se"]
        coin = read_coinday(P[(side, v)], side) or {}
        tot = {c: float(a[sl].sum()) for c, a in coin.items()}
        best2 = [c for c, _ in sorted(tot.items(), key=lambda kv: -kv[1])[:2]]
        worst2 = [c for c, _ in sorted(tot.items(), key=lambda kv: kv[1])[:2]]
        sb_, sw_ = x.copy(), x.copy()
        for c in best2:
            sb_ = sb_ - coin[c][sl]
        for c in worst2:
            sw_ = sw_ - coin[c][sl]
        st["no_best2_days"] = stat(np.delete(x, np.argsort(-x)[:2]), r)
        st["no_worst2_days"] = stat(np.delete(x, np.argsort(x)[:2]), r)
        st["no_best2_coins"] = stat(sb_, r)
        st["no_worst2_coins"] = stat(sw_, r)
        after = np.zeros(30)
        before = np.zeros(30)
        for c, a in coin.items():
            f = fq.get(c)
            for d in range(30):
                day = d + (0 if h == "A" else 30)
                if f is not None and day > f:
                    after[d] += a[day]
                else:
                    before[d] += a[day]
        st["after_first_day"] = stat(after, r)
        st["before_first_day"] = float(before.mean())
        st["best_coins"], st["worst_coins"] = best2, worst2
        st["worst_day"] = float(x.min())
        st["kpi"] = drawdown_kpi(x)
        tr = [t for t in read_trades(P[(side, v)], side) if (int(t["entry_day"]) < 30) == (h == "A")]
        n = len(tr)
        st["trades"] = n
        st["trades_per_day"] = n / 30
        st["stop_share"] = (sum(1 for t in tr if t["kind"] == "stop") / n) if n else 0.0
        st["win_rate"] = (sum(1 for t in tr if float(t["net"]) > 0) / n) if n else 0.0
        st["usd_per_trade"] = (sum(float(t["net"]) for t in tr) / n) if n else 0.0
        st["bps_per_trade"] = st["usd_per_trade"] / 500 * 1e4
        meta = json.load(open(os.path.join(P[(side, v)], "meta.json")))[side]
        st["tick_signals"] = meta["signals_AB"][0 if h == "A" else 1]
        out[h] = st
    return out


def verdict(hs, z_h):
    hA, hB = hs["A"], hs["B"]

    def ok(s):
        return s["mean"] > 0 and s["low_holm"] > 0

    def ok_sub(s):
        return s["mean"] > 0 and s["mean"] - z_h * s["se"] > 0

    rob = all(ok_sub(h_[k]) for h_ in (hA, hB) for k in ("no_best2_days", "no_best2_coins", "after_first_day"))
    conf = ok(hA) and ok(hB) and rob

    def neg(s):
        return s["mean"] < 0 or s["hi"] < 0

    def neg_rob(s):
        return s["mean"] < 0 and s["no_worst2_days"]["mean"] < 0 and s["no_worst2_coins"]["mean"] < 0

    ref = (neg(hA) and neg_rob(hA)) or (neg(hB) and neg_rob(hB))
    zero = hA["trades"] + hB["trades"] == 0
    return "подтверждено" if conf else ("опровергнуто" if ref else ("не ясно (0 сделок)" if zero else "не ясно"))


def main():
    name = sys.argv[1]
    base_only = "--base-only" in sys.argv
    tdir = os.path.join(ROOT, name)
    D, P, T = load_all([tdir])
    r = rng()
    fq = fq_days()
    rep1 = json.load(open(os.path.join(ROOT, "report.json")))
    top = json.load(open(os.path.join(ROOT, "top3.json")))
    out = {}
    for side in SIDES:
        if not D[side]:
            continue
        m_a = rep1[side]["m_package"]
        cands = {"base": ("b0", "b0")}
        if not base_only:
            if "cA_" + side in D[side] and "cB_" + side in D[side]:
                cands["crossfit"] = ("cB_" + side, "cA_" + side)
            if "uA_" + side in D[side] and "uB_" + side in D[side]:
                same = all(np.array_equal(D[side]["cA_" + side], D[side]["uA_" + side]) for _ in [0]) and all(
                    np.array_equal(D[side]["cB_" + side], D[side]["uB_" + side]) for _ in [0])
                if not same:
                    cands["letter"] = ("uB_" + side, "uA_" + side)
            for t in top[side]:
                if t["vid"] in D[side]:
                    cands["top:" + t["vid"]] = (t["vid"], t["vid"])
        n_b = sum(1 for k in cands if k != "letter") + (1 if "crossfit" in cands else 0)   # база 1 + перекрёстный 2 + топ-3
        m = m_a + n_b
        z_h = ND.inv_cdf(1 - 0.05 / (2 * m))
        res = {"m_step_a": m_a, "n_step_b": n_b, "m": m, "z_holm": z_h, "cand": {}}
        for k, (vA, vB) in cands.items():
            hs = halves_stats(side, P, D, vA, vB, z_h, fq, r)
            res["cand"][k] = {"vA": vA, "vB": vB, "halves": hs, "verdict": verdict(hs, z_h)}
        # справочно: задержка и проскальзывание (не испытания)
        sens = {}
        for base_id, (vA, vB) in [("b0", ("b0", "b0"))] + ([("cross", ("cB_" + side, "cA_" + side))] if not base_only else []):
            for tag in ("lat250", "lat1000", "slip0", "slip2", "slip5"):
                if base_id == "b0":
                    va = vb = "b0_" + tag
                else:
                    va, vb = "cB_%s_%s" % (side, tag), "cA_%s_%s" % (side, tag)
                if va in D[side] and vb in D[side]:
                    xa, xb = D[side][va][A], D[side][vb][B]
                    e = {"A": float(xa.mean()), "B": float(xb.mean())}
                    for h, vid, x in (("A", va, xa), ("B", vb, xb)):
                        st = stat(x, r)
                        tr = [t for t in read_trades(P[(side, vid)], side) if (int(t["entry_day"]) < 30) == (h == "A")]
                        e["stat" + h] = {"lo": st["lo"], "hi": st["hi"], "trades": len(tr), "trades_per_day": len(tr) / 30}
                    sens[base_id + "_" + tag] = e
        res["sens"] = sens
        out[side] = res
        print("== %s: m=%d (шаг A %d + шаг B %d), z=%.2f" % (side, m, m_a, n_b, z_h))
        for k, c in res["cand"].items():
            a, b = c["halves"]["A"], c["halves"]["B"]
            print("  [%s] %s  A %+.2f [%.2f; %.2f] n=%d (%.2f/сут) lowH %.2f | B %+.2f [%.2f; %.2f] n=%d (%.2f/сут) lowH %.2f | стопов %.0f%%/%.0f%% | bps/сд %.1f/%.1f" % (
                k, c["verdict"], a["mean"], a["lo"], a["hi"], a["trades"], a["trades_per_day"], a["low_holm"],
                b["mean"], b["lo"], b["hi"], b["trades"], b["trades_per_day"], b["low_holm"], a["stop_share"] * 100, b["stop_share"] * 100,
                a["bps_per_trade"], b["bps_per_trade"]))
        for k, v in sens.items():
            print("   sens %s: A %+.2f [%.2f; %.2f] n=%d (%.2f/сут) | B %+.2f [%.2f; %.2f] n=%d (%.2f/сут)" % (
                k, v["A"], v["statA"]["lo"], v["statA"]["hi"], v["statA"]["trades"], v["statA"]["trades_per_day"],
                v["B"], v["statB"]["lo"], v["statB"]["hi"], v["statB"]["trades"], v["statB"]["trades_per_day"]))
    json.dump(out, open(os.path.join(ROOT, "report-%s.json" % name), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


if __name__ == "__main__":
    main()
