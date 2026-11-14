"""П-42: итоговые числа по сторонам → data/p42/report.json и data/p42/report-tables.md (для отчёта docs/findings).

  python tools/p42/report.py
Читает все прогоны в data/p42/*/ (stage1, ext, joint, final); кандидаты — `phase.decide` (те же правила §9).
"""
import csv
import datetime as dt
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from axes import AXES, BASE, fmt, vid  # noqa: E402
from lib import (A, B, HALVES, ND, ROOT, SIDES, drawdown_kpi, holm, load_all, read_coinday, read_trades, stat,  # noqa: E402
                 sb, vid_of)
from phase import decide, rng  # noqa: E402

DAY0 = dt.date(2026, 9, 15)


def fq_days():
    """Индекс суток (от 15.09) первого дня с оборотом ≥ $100 млн; < 0 — прошла порог до окна (все сутки «после»)."""
    out = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "first_qualifying_day.csv"))):
        d = r["first_day_ge_overall"]
        if not d:
            out[r["symbol"]] = None
            continue
        idx = (dt.date.fromisoformat(d) - DAY0).days
        out[r["symbol"]] = -1 if idx < 0 else idx
    return out


def concat(x_for_A, x_for_B):
    return np.concatenate([x_for_A[A], x_for_B[B]])


def cand_vids(side, kind):
    """(вариант для суток A, вариант для суток B): A-сутки — конфиг, выбранный на B (cB), и наоборот."""
    if kind == "base":
        return "b0", "b0"
    if kind == "letter":
        return "uB_" + side, "uA_" + side
    return "cB_" + side, "cA_" + side


def coin_concat(P, side, vA, vB):
    ca, cb = read_coinday(P[(side, vA)], side), read_coinday(P[(side, vB)], side)
    out = {}
    for c in set(ca) | set(cb):
        a = ca.get(c, np.zeros(60))
        b = cb.get(c, np.zeros(60))
        out[c] = np.concatenate([a[:30], b[30:]])
    return out


FUNNEL_VARIANTS = (  # база и три «ослабленных» фильтра (варианты уже посчитаны: шаг 1 и расширения, новых испытаний нет)
    ("b0", "база"),
    ("v_ban_0", "запрет ложного пробоя выключен (ban = 0)"),
    ("v_natr_min_0p2", "NATR мин = 0,2 %"),
    ("v_t1h_0p1", "оборот за час мин = $0,1 млн"),
)


def funnel(D, P, side):
    """Воронка фильтров и частота сделок по половинам: счётчики `meta.json` + сделки варианта (A/B — по дню входа)."""
    out = {}
    for v, label in FUNNEL_VARIANTS:
        meta = json.load(open(os.path.join(P[(side, v)], "meta.json")))[side]
        tr = read_trades(P[(side, v)], side)
        halves = {}
        for hi, h in enumerate("AB"):
            sub = [t for t in tr if (int(t["entry_day"]) < 30) == (h == "A")]
            n = len(sub)
            crossed = meta["levels_crossed_AB"][hi]
            sig = meta["signals_AB"][hi]
            halves[h] = {
                "crossed": crossed,
                "geo_pass": crossed - meta["levels_skipped_geo_AB"][hi],
                "pool_pass": crossed - meta["levels_skipped_AB"][hi],
                "signals": sig, "rejected": sig - n, "trades": n, "trades_per_day": n / 30,
                "win_rate": (sum(1 for t in sub if float(t["net"]) > 0) / n) if n else 0.0,
                "stop_share": (sum(1 for t in sub if t["kind"] == "stop") / n) if n else 0.0,
                "usd_per_trade": (sum(float(t["net"]) for t in sub) / n) if n else 0.0,
                "per_day": float(D[side][v][HALVES[h]].mean()),
            }
        out[v] = {"label": label, "halves": halves,
                  "rej_kinds": dict(zip(("consumed", "busy", "coin_pause", "global_pause", "cap", "no_outcome"),
                                        meta["rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]"]))}
    return out


def holds(x, z_h, r):
    st = stat(x, r)
    return st["mean"] > 0 and st["mean"] - z_h * st["se"] > 0, st


def main():
    D, P, T = load_all()
    r = rng()
    fq = fq_days()
    report = {}
    md = []
    for side in SIDES:
        out, cfg, joint, tests = decide(D, side, r, T)
        sd = D[side]
        # ---- кандидаты
        have_cf = bool(cfg["A"] or cfg["B"]) and ("cA_" + side) in sd and ("cB_" + side) in sd
        have_l = ("uA_" + side) in sd and ("uB_" + side) in sd
        kinds = ["base"] + (["crossfit"] if have_cf else []) + (["letter"] if have_l else [])
        cand = {}
        for k in kinds:
            vA, vB = cand_vids(side, k)
            x = concat(sd[vA], sd[vB])
            cand[k] = {"vA": vA, "vB": vB, "x": x, "coin": coin_concat(P, side, vA, vB)}
        # ---- число испытаний на сторону
        n_ext = json.load(open(os.path.join(ROOT, "plan-ext.json")))[side]
        n_joint = len(json.load(open(os.path.join(ROOT, "plan-joint.json")))[side]["new_trials"])
        n_stage1 = len([v for v in AXES for x in AXES[v] if x != BASE[v]])
        trials = n_stage1 + n_ext + n_joint + (1 if have_cf else 0) + (1 if have_l else 0)
        # ---- проверки пакета для Холма: валидации осей и совместные + 2 половины × кандидаты
        tests_pkg = [t for t in tests if t["side"] == side]
        cstats = {}
        for k in kinds:
            for h in "AB":
                st = stat(cand[k]["x"][HALVES[h]], r)
                cstats[(k, h)] = st
                tests_pkg.append({"side": side, "kind": "cand", "ax": k, "dir": h, "p": st["p"]})
        # семейство Холма = все испытания стороны (счётчик): непроверенные гипотезы входят с p = 1 (замечание проверяющего, п. 4)
        m_tests = len(tests_pkg)
        m = max(trials, m_tests)
        adj = holm([t["p"] for t in tests_pkg] + [1.0] * (m - m_tests))
        for t, a in zip(tests_pkg, adj):
            t["p_holm"] = a
        z_h = ND.inv_cdf(1 - 0.05 / (2 * m))
        # ---- таблицы осей
        rows = []
        for ax, x in out.items():
            for i, v in enumerate(x["vals"]):
                vv = vid_of(ax, v)
                rows.append({
                    "ax": ax, "value": fmt(v), "base": v == BASE[ax],
                    "meanA": float(sd[vv][A].mean()), "meanB": float(sd[vv][B].mean()),
                    "nA": T[side][vv][0], "nB": T[side][vv][1],
                    "selA": x["half"]["A"]["sel"] == i, "selB": x["half"]["B"]["sel"] == i,
                })
        with open(os.path.join(ROOT, "axis-table-%s.csv" % side), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        # ---- вердикт по кандидатам
        res = {"trials": trials, "m_package": m, "m_tests": m_tests, "z_holm": z_h, "cfg": cfg, "joint": None if joint is None else {
            "accepted": joint["accepted"], "same_pair": joint["same_pair"], "adjacent": joint["adjacent"],
            "pair": joint["pair"], "cellA": joint["A"]["cell"], "cellB": joint["B"]["cell"]}, "cand": {}}
        for k in kinds:
            c = cand[k]
            cr = {"halves": {}}
            for h in "AB":
                st = cstats[(k, h)]
                x = c["x"][HALVES[h]]
                st["low_holm"] = st["mean"] - z_h * st["se"]
                st["p_holm"] = [t for t in tests_pkg if t["kind"] == "cand" and t["ax"] == k and t["dir"] == h][0]["p_holm"]
                # без 2 лучших суток / 2 лучших монет
                xs = np.sort(x)
                no_best_days = np.delete(x, np.argsort(-x)[:2])
                no_worst_days = np.delete(x, np.argsort(x)[:2])
                coin_tot = {cn: float(a[HALVES[h]].sum()) for cn, a in c["coin"].items()}
                best2 = [cn for cn, _ in sorted(coin_tot.items(), key=lambda kv: -kv[1])[:2]]
                worst2 = [cn for cn, _ in sorted(coin_tot.items(), key=lambda kv: kv[1])[:2]]
                sub = x.copy()
                for cn in best2:
                    sub = sub - c["coin"][cn][HALVES[h]]
                sub_w = x.copy()
                for cn in worst2:
                    sub_w = sub_w - c["coin"][cn][HALVES[h]]
                # до / после первого дня ≥ $100 млн
                after = np.zeros(30)
                before = np.zeros(30)
                for cn, a in c["coin"].items():
                    f = fq.get(cn)
                    for d in range(30):
                        day = d + (0 if h == "A" else 30)
                        if f is not None and day > f:
                            after[d] += a[day]
                        else:
                            before[d] += a[day]
                st["no_best2_days"] = stat(no_best_days, r) if len(no_best_days) else None
                st["no_best2_coins"] = stat(sub, r)
                st["after_first_day"] = stat(after, r)
                st["before_first_day"] = {"mean": float(before.mean())}
                st["no_worst2_days"] = stat(no_worst_days, r)
                st["no_worst2_coins"] = stat(sub_w, r)
                st["best_coins"] = best2
                st["worst_coins"] = worst2
                st["worst_day"] = float(x.min())
                st["best_day"] = float(x.max())
                st["nonzero_days"] = int((np.abs(x) > 1e-12).sum())
                st["kpi"] = drawdown_kpi(x)
                sr = float(x.mean() / x.std(ddof=1)) if x.std(ddof=1) > 0 else 0.0
                se_sr = math.sqrt((1 + 0.5 * sr * sr) / len(x))
                st["sharpe_daily"] = sr
                st["dsr"] = sb.significance(sr, se_sr, trials)
                # сделки половины (конфиг, оценённый на этой половине)
                v = c["vA"] if h == "A" else c["vB"]
                tr = [t for t in read_trades(P[(side, v)], side) if (int(t["entry_day"]) < 30) == (h == "A")]
                n = len(tr)
                st["trades"] = n
                st["stop_share"] = (sum(1 for t in tr if t["kind"] == "stop") / n) if n else 0.0
                st["win_rate"] = (sum(1 for t in tr if float(t["net"]) > 0) / n) if n else 0.0
                st["trades_per_day"] = n / 30
                st["usd_per_trade"] = (sum(float(t["net"]) for t in tr) / n) if n else 0.0
                st["bps_per_trade"] = st["usd_per_trade"] / 500 * 1e4
                st["share_before_fq"] = (sum(1 for t in tr if (fq.get(t["sym"]) is not None and int(t["entry_day"]) <= fq[t["sym"]])) / n) if n else 0.0
                meta = json.load(open(os.path.join(P[(side, v)], "meta.json")))[side]
                hi = 0 if h == "A" else 1
                st["skip_share"] = meta["levels_skipped_geo_AB"][hi] / meta["levels_crossed_AB"][hi] if meta["levels_crossed_AB"][hi] else None
                st["skip_share_pool"] = meta["levels_skipped_AB"][hi] / meta["levels_crossed_AB"][hi] if meta["levels_crossed_AB"][hi] else None
                st["signals"] = meta["signals_AB"][hi]
                cr["halves"][h] = st
            hA, hB = cr["halves"]["A"], cr["halves"]["B"]

            def ok(st):
                return st["mean"] > 0 and st["low_holm"] > 0

            def ok_sub(s_):
                return s_ is not None and s_["mean"] > 0 and s_["mean"] - z_h * s_["se"] > 0

            conf = ok(hA) and ok(hB)
            rob = all(ok_sub(h_[kk]) for h_ in (hA, hB) for kk in ("no_best2_days", "no_best2_coins", "after_first_day"))

            def neg(st):
                return st["mean"] < 0 or st["hi"] < 0

            def neg_robust(st):
                return st["mean"] < 0 and st["no_worst2_days"]["mean"] < 0 and st["no_worst2_coins"]["mean"] < 0

            refuted = (neg(hA) and neg_robust(hA)) or (neg(hB) and neg_robust(hB))
            # устойчивость опровержения по половинам: знак «−» (п. 10) и без 2 худших суток / монет
            cr["refut_by_half"] = {h_: {"neg": bool(neg(st_)), "robust": bool(neg(st_) and neg_robust(st_))} for h_, st_ in (("A", hA), ("B", hB))}
            cr["conf_applicable"] = bool(ok(hA) and ok(hB))
            zero = hA["trades"] + hB["trades"] == 0
            cr["confirmed"] = bool(conf and rob)
            cr["refuted"] = bool(refuted)
            cr["verdict"] = "подтверждено" if cr["confirmed"] else ("опровергнуто" if refuted else "не ясно")
            cr["zero_trades"] = zero
            cr["robust_ok"] = bool(rob)
            # безубыточное проскальзывание
            pts = []
            for sl in (0, 1, 2, 5):
                if k == "base":
                    vv = "b0" if sl == 1 else "b0_slip%d" % sl
                    if vv not in sd:
                        continue
                    pts.append((sl, float(sd[vv].mean())))
                elif k == "crossfit":
                    va = "cB_%s" % side if sl == 1 else "cB_%s_slip%d" % (side, sl)
                    vb = "cA_%s" % side if sl == 1 else "cA_%s_slip%d" % (side, sl)
                    if va not in sd or vb not in sd:
                        continue
                    pts.append((sl, float(concat(sd[va], sd[vb]).mean())))
            cr["slip_points"] = pts
            be = None
            for (s0, m0), (s1, m1) in zip(pts, pts[1:]):
                if m0 > 0 >= m1:
                    be = s0 + (s1 - s0) * m0 / (m0 - m1)
                    break
            cr["breakeven_slip_bps"] = be
            # лучший порядок (справочно)
            if k == "base" and "b0_best" in sd:
                cr["best_order"] = {"A": float(sd["b0_best"][A].mean()), "B": float(sd["b0_best"][B].mean())}
            elif k == "crossfit" and "cA_%s_best" % side in sd and "cB_%s_best" % side in sd:
                xb = concat(sd["cB_%s_best" % side], sd["cA_%s_best" % side])
                cr["best_order"] = {"A": float(xb[A].mean()), "B": float(xb[B].mean())}
            res["cand"][k] = cr
        # ---- PBO по таблице шага 1 (все варианты стороны)
        names = [v for v in sd if v == "b0" or v.startswith("v_")]
        M = np.stack([sd[v] for v in names], axis=1)
        p = sb.pbo(M, n_splits=10)
        res["pbo"] = {kk: (vv if not isinstance(vv, (np.ndarray, list)) else None) for kk, vv in p.items()}
        res["pbo_n_configs"] = len(names)
        res["funnel"] = funnel(D, P, side)
        report[side] = res
    json.dump(report, open(os.path.join(ROOT, "report.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    # ---- краткая печать
    for side in SIDES:
        res = report[side]
        print("== %s: испытаний %d, семейство Холма m=%d (проверок в пакете %d), z_Холм=%.2f, PBO=%s" % (side, res["trials"], res["m_package"], res["m_tests"], res["z_holm"], res["pbo"].get("pbo")))
        print("   cfg A→B:", res["cfg"]["A"], "\n   cfg B→A:", res["cfg"]["B"], "\n   joint:", res["joint"])
        for k, c in res["cand"].items():
            hA, hB = c["halves"]["A"], c["halves"]["B"]
            print("  [%s] %s: A mean %.2f [%.2f; %.2f] low_holm %.2f n=%d | B mean %.2f [%.2f; %.2f] low_holm %.2f n=%d | robust=%s zero=%s be_slip=%s" % (
                k, c["verdict"], hA["mean"], hA["lo"], hA["hi"], hA["low_holm"], hA["trades"], hB["mean"], hB["lo"], hB["hi"], hB["low_holm"], hB["trades"],
                c["robust_ok"], c["zero_trades"], c["breakeven_slip_bps"]))


if __name__ == "__main__":
    main()
