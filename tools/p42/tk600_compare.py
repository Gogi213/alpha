"""П-42 §13.25 п. 5 (TK-600), этап 1: сравнение руки X и руки f12 по целям TK-598 на внефолдовых предсказаниях F (метки читаются только здесь).

  python3 tk600_compare.py stage1 --tab-dir <tab-X-F-*.parquet> --oof-dir <oof-*.parquet> --fit-json-X <fit-long.json,fit-short.json> --out <каталог результата>
      на сторону и цель: `D = S0_X − S0_f12` на U0-F (одни строки; границы `q_{2/3}` (T4 — `q_{1/3}`) каждой руки — по собственным OOF по U0-F); блочный бутстреп по суткам
      (блок 4, 20 000, seed 600, одни индексы суток на обе руки), `p_D = P(D* ≤ 0)`, Холм по определённым целям стороны; (б) знак по 5 блокам F; (в) без 2 лучших суток / монет;
      (г) выбранных ≥ 30; допуск на этап 2 — `D_U1-F-OOF > 0` и выбранных ≥ 30 у обеих рук (границы `q` — U1-F OOF каждой руки). Описательно: ΔAUC / ΔSpearman (1000 пересэмплирований),
      D на U1-F с интервалом, важности групп признаков X-руки, MDE_U0 = 2,485·SE(D).
  python3 tk600_compare.py coverage --feat-dir <каталог feat-X-*.parquet> --out <csv>      (G-C: покрытие X по окнам; первые 7 суток окна — отдельно)
Результат — `stage1.json`, `stage1.csv`; в stdout — короткая таблица. Медианы стратегии не печатаются.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk600_feat as F6  # noqa: E402

SEED = 600
B = 20_000
B_DESC = 1_000
BLOCK = 4
NDAYS, D_FIRST = 60, 14
Z_MDE = 1.6449 + 0.84
GROUPS = {"f12": F6.F12, "FLOW16": F6.F4.FLOW, "DERIV14": F6.F4.DERIV, "TOX13": F6.TOX}
LABELS = ["net_nf", "bps", "y_win", "y_stop", "mfe_natr", "mae_natr"]


def arm_sel(s, lower):
    q = L.oof_threshold(s, lower)
    return q, L.pick_selected(s, q, lower)


def mean_sel(net, sel):
    return float(net[sel].mean()) if sel.any() else float("nan")


def drop_top_d(net, selx, self_, groups, n_drop=2):
    """D без `n_drop` групп с наибольшим вкладом в D; вклад группы = Σ_g net·sel_X / n_X − Σ_g net·sel_f / n_f; границы `q` не пересчитываются."""
    nx, nf = int(selx.sum()), int(self_.sum())
    contrib = pd.Series(net * selx / max(nx, 1) - net * self_ / max(nf, 1)).groupby(np.asarray(groups)).sum().sort_values(ascending=False)
    keep = ~np.isin(groups, contrib.index[:n_drop].to_numpy())
    return mean_sel(net[keep], selx[keep]) - mean_sel(net[keep], self_[keep]), [str(g) for g in contrib.index[:n_drop]]


def metric_of(t, s, d):
    if t == "T3":
        return L.auc(d["y_win"].to_numpy(), s)
    if t == "T4":
        return L.auc(d["y_stop"].to_numpy(), s)
    if t == "T2":
        return L.spearman(s, d["bps"].to_numpy(float))
    return L.spearman(s, d["net_nf"].to_numpy(float))


def desc_metric_delta(t, d, sx, sf, day):
    """ΔAUC (T3, T4) / ΔSpearman(ŝ, net_nf или bps) X − f12: точка и 95 % интервал по суткам (1000 пересэмплирований блоками)."""
    point = metric_of(t, sx, d) - metric_of(t, sf, d)
    idx = L.boot_indices(NDAYS, B_DESC, BLOCK, SEED)
    rows_by_day = [np.nonzero(day == D_FIRST + i)[0] for i in range(NDAYS)]
    vals = []
    for r in range(idx.shape[0]):
        rows = np.concatenate([rows_by_day[i] for i in idx[r]])
        dd = d.iloc[rows]
        vals.append(metric_of(t, sx[rows], dd) - metric_of(t, sf[rows], dd))
    v = np.array(vals, float)
    return {"delta": float(point), "ci95": [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))],
            "auc_or_rho_X": float(metric_of(t, sx, d)), "auc_or_rho_f12": float(metric_of(t, sf, d))}


def load_side(side, tab_dir, oof_dir, univ):
    v = "L" if side == "long" else "S"
    tab = pd.read_parquet(os.path.join(tab_dir, f"tab-X-F-{v}_{univ}.parquet"), columns=L.KEY + ["day", "net_nf", "bps", "y_win", "y_stop", "mfe_natr", "mae_natr"])
    ox = pd.read_parquet(os.path.join(oof_dir, f"oof-X-{side}-{univ}.parquet")).drop(columns=["day"])
    of = pd.read_parquet(os.path.join(oof_dir, f"oof-f12-{side}-{univ}.parquet")).drop(columns=["day"])
    tg = L.TARGETS[side]
    ox = ox.rename(columns={f"s_{t}": f"sX_{t}" for t in tg})
    of = of.rename(columns={f"s_{t}": f"sF_{t}" for t in tg})
    df = tab.merge(ox, on=L.KEY, how="left", validate="one_to_one").merge(of, on=L.KEY, how="left", validate="one_to_one")
    if len(df) != len(tab) or df[[f"sX_{t}" for t in tg]].isna().all(axis=None) or df[[f"sF_{t}" for t in tg]].isna().all(axis=None):
        raise SystemExit(f"G-K: строки OOF не совпали с таблицей ({side} {univ})")
    for t in tg:
        if df[f"sX_{t}"].isna().any() or df[f"sF_{t}"].isna().any():
            raise SystemExit(f"G-K: пропуски ŝ ({side} {univ} {t})")
    return df


def group_importance(fit_json):
    """Доли PredictionValuesChange финальных моделей X-руки по группам признаков (описательно): среднее по подмоделям."""
    if not fit_json or not os.path.exists(fit_json):
        return None
    res = json.load(open(fit_json, encoding="utf-8"))
    out = {}
    for t, td in res.get("targets", {}).items():
        for sub, sd in td.get("subs", {}).items():
            pvc = (sd.get("final") or {}).get("pvc")
            if not pvc:
                continue
            tot = sum(pvc.values()) or 1.0
            out[f"{t}/{sub}"] = {g: float(sum(pvc.get(f, 0.0) for f in fs) / tot) for g, fs in GROUPS.items()}
    return out


def stage1_side(side, tab_dir, oof_dir, fit_json):
    u0 = load_side(side, tab_dir, oof_dir, "u0")
    u1 = load_side(side, tab_dir, oof_dir, "u1")
    day0, net0 = u0["day"].to_numpy(), u0["net_nf"].to_numpy(float)
    day1, net1 = u1["day"].to_numpy(), u1["net_nf"].to_numpy(float)
    blk0 = L.block_of(day0)
    idx = L.boot_indices(NDAYS, B, BLOCK, SEED)
    stops = int(u0["y_stop"].sum())
    rows, pvals, names = [], [], []
    for t in L.TARGETS[side]:
        lower = t == "T4"
        r = {"side": side, "target": t, "lower": lower, "n_U0": int(len(u0)), "n_U1": int(len(u1)), "sigma_U0": float(net0.std(ddof=1)), "mean_all_U0": float(net0.mean())}
        if t == "T4" and stops < L.MIN_EVENTS:
            r["undefined"] = f"стопов в U0-F {stops} < {L.MIN_EVENTS}"
            rows.append(r)
            continue
        sx, sf = u0[f"sX_{t}"].to_numpy(float), u0[f"sF_{t}"].to_numpy(float)
        qx, selx = arm_sel(sx, lower)
        qf, self_ = arm_sel(sf, lower)
        _, _, ssx, csx = L.day_series(day0, net0, selx, NDAYS, D_FIRST)
        _, _, ssf, csf = L.day_series(day0, net0, self_, NDAYS, D_FIRST)
        bx, bf = L.boot_mean_sel(ssx, csx, idx), L.boot_mean_sel(ssf, csf, idx)
        db = bx - bf
        s0x, s0f = mean_sel(net0, selx), mean_sel(net0, self_)
        d = s0x - s0f
        p = L.p_one_sided(db, "le0")
        se = float(np.nanstd(db, ddof=1))
        blocks = []
        for k in range(5):
            m = blk0 == k
            blocks.append(mean_sel(net0[m], selx[m]) - mean_sel(net0[m], self_[m]))
        dd, dtop = drop_top_d(net0, selx, self_, day0)
        dc, ctop = drop_top_d(net0, selx, self_, u0["sym"].to_numpy())
        r.update({"q_X": qx, "q_f12": qf, "n_sel_X": int(selx.sum()), "n_sel_f12": int(self_.sum()), "S0_X": s0x, "S0_f12": s0f, "D": d,
                  "sum_sel_X": float(net0[selx].sum()), "sum_sel_f12": float(net0[self_].sum()), "p_D": p, "SE_D": se, "MDE_U0": Z_MDE * se,
                  "D_ci95": [float(np.nanpercentile(db, 2.5)), float(np.nanpercentile(db, 97.5))], "D_lower5": float(np.nanpercentile(db, 5)),
                  "D_blocks": blocks, "blocks_pos": int(sum(1 for x in blocks if x == x and x > 0)),
                  "D_drop2days": dd, "dropped_days": dtop, "D_drop2coins": dc, "dropped_coins": ctop,
                  "model_MDE_U0_0p056_sigma": 0.056 * r["sigma_U0"]})
        r["desc"] = desc_metric_delta(t, u0, sx, sf, day0)
        # U1-F: границы каждой руки — по её U1-F OOF (как ворота OOF §13.23)
        s1x, s1f = u1[f"sX_{t}"].to_numpy(float), u1[f"sF_{t}"].to_numpy(float)
        q1x, sel1x = arm_sel(s1x, lower)
        q1f, sel1f = arm_sel(s1f, lower)
        _, _, a1, b1 = L.day_series(day1, net1, sel1x, NDAYS, D_FIRST)
        _, _, c1, e1 = L.day_series(day1, net1, sel1f, NDAYS, D_FIRST)
        d1b = L.boot_mean_sel(a1, b1, idx) - L.boot_mean_sel(c1, e1, idx)
        r["U1F"] = {"n_sel_X": int(sel1x.sum()), "n_sel_f12": int(sel1f.sum()), "S0_X": mean_sel(net1, sel1x), "S0_f12": mean_sel(net1, sel1f),
                    "D": mean_sel(net1, sel1x) - mean_sel(net1, sel1f), "D_ci95": [float(np.nanpercentile(d1b, 2.5)), float(np.nanpercentile(d1b, 97.5))],
                    "sum_sel_X": float(net1[sel1x].sum()), "sum_sel_f12": float(net1[sel1f].sum()), "mean_all": float(net1.mean()), "n": int(len(net1)),
                    "q_X": q1x, "q_f12": q1f}
        rows.append(r)
        pvals.append(p)
        names.append(t)
    rej = L.holm_reject(pvals) if pvals else []
    holm = dict(zip(names, rej))
    for r in rows:
        if "undefined" in r:
            r["verdict"] = "не определена"
            continue
        a_ = bool(holm[r["target"]])
        b_ = r["blocks_pos"] >= 4
        c_ = r["D_drop2days"] > 0 and r["D_drop2coins"] > 0
        g_ = r["n_sel_X"] >= L.MIN_SEL and r["n_sel_f12"] >= L.MIN_SEL
        r["holm_reject"], r["rule_b"], r["rule_c"], r["rule_g"] = a_, b_, c_, g_
        r["m"] = len(names)
        if a_ and b_ and c_ and g_:
            r["verdict"] = "добавляет"
        elif a_:
            r["verdict"] = "не ясно"
        else:
            r["verdict"] = "не добавляет при различимом D ≥ MDE_U0"
        u = r["U1F"]
        r["admit_stage2"] = bool(r["verdict"] == "добавляет" and u["D"] > 0 and u["n_sel_X"] >= L.MIN_SEL and u["n_sel_f12"] >= L.MIN_SEL)
    return {"side": side, "rows_U0": int(len(u0)), "rows_U1": int(len(u1)), "stops_U0": stops, "targets": rows, "group_importance_X": group_importance(fit_json)}


def cmd_stage1(a):
    os.makedirs(a.out, exist_ok=True)
    fj = dict(zip(("long", "short"), (a.fit_json_x.split(",") + ["", ""])[:2]))
    res = {"protocol": "docs/research/P-42-level-breakout.md §13.25", "seed": SEED, "B": B, "B_desc": B_DESC, "block_days": BLOCK, "sides": {}}
    flat = []
    for side in ("long", "short"):
        sd = stage1_side(side, a.tab_dir, a.oof_dir, fj[side])
        res["sides"][side] = sd
        for r in sd["targets"]:
            flat.append({k: v for k, v in r.items() if not isinstance(v, (dict, list))} | {"U1F_D": (r.get("U1F") or {}).get("D")})
    with open(os.path.join(a.out, "stage1.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    pd.DataFrame(flat).to_csv(os.path.join(a.out, "stage1.csv"), index=False)
    print("side target | S0_X S0_f12 D [lo5] p_D | n_sel X/f | blocks drop_d drop_c | verdict | U1F D")
    for side in ("long", "short"):
        for r in res["sides"][side]["targets"]:
            if "undefined" in r:
                print(side, r["target"], r["undefined"])
                continue
            print(f"{side:5s} {r['target']} | {r['S0_X']:+.3f} {r['S0_f12']:+.3f} {r['D']:+.3f} [{r['D_lower5']:+.3f}] {r['p_D']:.4f} | {r['n_sel_X']}/{r['n_sel_f12']} | "
                  f"{r['blocks_pos']}/5 {r['D_drop2days']:+.3f} {r['D_drop2coins']:+.3f} | {r['verdict']} | {r['U1F']['D']:+.3f} adm={r['admit_stage2']}")


def cmd_coverage(a):
    rows = []
    for fn in sorted(os.listdir(a.feat_dir)):
        if not (fn.startswith("feat-X-") and fn.endswith(".parquet")):
            continue
        df = pd.read_parquet(os.path.join(a.feat_dir, fn))
        first7 = (df["sig_ms"] // L.DAY) <= 20
        for c in F6.X_FEATS:
            rows.append({"file": fn, "feature": c, "group": next(g for g, fs in GROUPS.items() if c in fs), "rows": int(len(df)), "cov_all": float(df[c].notna().mean()),
                         "rows_first7d": int(first7.sum()), "cov_first7d": float(df.loc[first7, c].notna().mean()) if first7.any() else float("nan")})
    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out.to_csv(a.out, index=False)
    low = out[(out.group == "TOX13") & (out.cov_all < 0.5)]
    print(f"строк покрытия {len(out)}; TOX13 с покрытием < 50 %: {len(low)}")
    if len(low):
        print(low[["file", "feature", "cov_all", "cov_first7d"]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("stage1")
    s.add_argument("--tab-dir", required=True); s.add_argument("--oof-dir", required=True); s.add_argument("--fit-json-x", default=""); s.add_argument("--out", required=True)
    c = sp.add_parser("coverage")
    c.add_argument("--feat-dir", required=True); c.add_argument("--out", required=True)
    a = ap.parse_args()
    {"stage1": cmd_stage1, "coverage": cmd_coverage}[a.cmd](a)


if __name__ == "__main__":
    main()
