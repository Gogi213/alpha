"""П-42 §13.23 / §13.23.1 / §13.23.2 (TK-598), шаг 3–4: чтение окон ЗАМОРОЖЕННЫМИ моделями; подтверждение — на объединённом окне 01.07–31.08.

  python3 tk598_read.py read --win jul|aug|hold --frozen <frozen.json> --frozen-sha256 <hex> --models <каталог models> --run <out/NAME> --feat-dir <каталог feat> --out <каталог чтения> [--procs 6]
      Одно чтение окна: требует файл заморозки, его sha256 (из коммита заморозки), sha256 кода и моделей; создаёт `READ-<окно>.started` (O_EXCL) и при повторе отказывает.
      Строит таблицу строк окна (сделки U1 + метки + f12 + MAE / MFE), ворота G-W (даты, сумма `net` = сумма суточного среза движка: печатаются только булевы итоги и |Δ|),
      `ŝ` = среднее пяти фолдовых моделей, «треть» по замороженной границе `q`, описательные меры по цели; сохраняет строки окна (`rows-<окно>-<сторона>.parquet`) для объединения.
      `--win F` — только проверка кода на открытых данных F (маркер и выход — в каталоге с `TEST`).
  python3 tk598_read.py pool --frozen <frozen.json> --frozen-sha256 <hex> --out <каталог чтения>
      Объединённое окно 01.07–31.08 (62 суток, одна серия): S1, S2, блочный бутстреп 4 суток (20 000, seed 598), p = max(p1, p0), Холм по `m` один раз, нижняя 95 %-граница S0; S2 — охранное,
      S2 без 2 лучших суток / монет, S1 > 0 отдельно в июле и августе, вердикт; наименьший различимый лифт до Холма и после. Метки не открываются заново (читаются сохранённые строки окон).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk598_table as TB  # noqa: E402
from tk598_fit import SPECS, predict, sha256  # noqa: E402

ENV = {  # окно → (T0, каталог aggTrades) — §13.23.1
    "F": ("2026-09-01", "/data/aggtrades/binance"),
    "jul": ("2026-06-17", "/data/p42-tk596/jul/aggtrades"),
    "aug": ("2026-07-18", "/data/p42-tk596/aug/aggtrades"),
    "hold": ("2026-10-31", "/data/p42-tk598/hold-in/aggtrades"),
}
POOL_WINS = ("jul", "aug")
POOL_DAYS = 62


def load_models(frozen, models_dir):
    """Фолдовые модели проходящих целей: {(side, sub): [5 моделей]} после проверки sha256."""
    from catboost import CatBoostClassifier, CatBoostRanker, CatBoostRegressor
    cls = {"rmse": CatBoostRegressor, "logloss": CatBoostClassifier, "wlogloss": CatBoostClassifier, "rank": CatBoostRanker}
    out = {}
    for side, fs in frozen["sides"].items():
        for t in fs.get("pass", []):
            for sub, sd in fs["targets"][t]["subs"].items():
                ms = []
                for f in sd["folds"]:
                    p = os.path.join(models_dir, f["file"])
                    if sha256(p) != f["sha256"]:
                        raise SystemExit(f"sha256 модели не совпал: {f['file']}")
                    m = cls[sd["kind"]]()
                    m.load_model(p)
                    ms.append(m)
                out[(side, sub)] = (sd["kind"], ms)
    return out


def score(models, side, t, X):
    """ŝ цели: среднее пяти фолдовых моделей; T5 — ŝ = MFE^ − MAE^."""
    def one(sub):
        kind, ms = models[(side, sub)]
        return np.mean([predict(m, kind, X) for m in ms], axis=0)
    return one("mfe") - one("mae") if t == "T5" else one(SPECS[t][0][0])


def verify(frozen_path, frozen_sha):
    if not os.path.exists(frozen_path):
        raise SystemExit("нет файла заморозки: чтение запрещено (G-S)")
    if sha256(frozen_path) != frozen_sha:
        raise SystemExit("sha256 файла заморозки не совпал с коммитом")
    fr = json.load(open(frozen_path))
    here = os.path.dirname(os.path.abspath(__file__))
    for fn, h in fr["code_sha256"].items():
        if sha256(os.path.join(here, fn)) != h:
            raise SystemExit(f"код изменён после заморозки: {fn}")
    return fr


def read_window(a):
    win = a.win
    test = win == "F"
    fr = verify(a.frozen, a.frozen_sha256)
    os.makedirs(a.out, exist_ok=True)
    marker = os.path.join(a.out, f"READ-{win}{'-TEST' if test else ''}.started")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"{marker} уже есть — повторное чтение запрещено")
    os.write(fd, (dt.datetime.now(dt.timezone.utc).isoformat() + "\n").encode())
    os.close(fd)
    models = load_models(fr, a.models)
    lo, hi, nd, t0 = L.WINDOWS[win]
    res = {"win": win, "started_utc": open(marker).read().strip(), "days": [lo, hi], "ndays_calendar": nd, "sides": {}}
    gw_ok = True
    for side, sd in (("long", "L"), ("short", "S")):
        fs = fr["sides"].get(side)
        if not fs or not fs.get("pass"):
            res["sides"][side] = {"skipped": "нет целей, прошедших ворота OOF"}
            continue
        variant = f"{sd}_u1"
        feat = os.path.join(a.feat_dir, f"feat-{os.path.basename(a.run.rstrip('/'))}-{variant}.parquet")
        path_df = None
        if "T5" in fs["pass"]:
            import tk598_path as P
            d = os.path.join(a.run, "eng", variant, f"{side}-detail.jsonl")
            path_df = P.run(d, t0, ENV[win][1], a.procs)
            ppath = os.path.join(a.out, f"path-{win}-{side}.parquet")
            path_df.to_parquet(ppath)
        else:
            ppath = None
        tab, info = TB.build_table(a.run, variant, win, feat, ppath)
        # G-W: даты и сумма net = сумма суточного среза движка (булевы итоги и |Δ|, без печати сумм)
        daily = pd.read_csv(os.path.join(a.run, "eng", variant, f"{side}-daily.csv"))
        raw = pd.read_csv(os.path.join(a.run, "eng", variant, f"{side}-trades.csv"), usecols=["net"])
        gw = {"rows": int(len(tab)), "dropped_outside_window": info["dropped_outside_window"], "distinct_sig_days": info["distinct_sig_days"],
              "sum_net_vs_daily_abs_diff": float(abs(raw["net"].sum() - float(daily["pnl"].sum())))}
        gw["ok"] = bool(gw["dropped_outside_window"] == 0 and gw["sum_net_vs_daily_abs_diff"] <= 0.01)
        gw_ok &= gw["ok"]
        X = tab[L.FEATS].to_numpy(np.float64)
        sres = {"G-W": gw, "rows_U1": int(len(tab)), "nan_share": info["nan_share"], "targets": {}}
        rows = tab[L.KEY + ["day", "net_nf", "y_win", "y_stop", "bps", "mfe_natr", "mae_natr", "natr_bp"]].copy() if "mfe_natr" in tab else \
            tab[L.KEY + ["day", "net_nf", "y_win", "y_stop", "bps", "natr_bp"]].copy()
        for t in fs["pass"]:
            td = fs["targets"][t]
            s = score(models, side, t, X)
            rows[f"s_{t}"] = s
            d = L.describe(tab, s, t, td["q"], td["lower"], nd)
            d["share_sel_vs_oof_third"] = d["sel_share"]
            sres["targets"][t] = d
        cn = fs["control_natr_bp"]
        sres["control_natr_bp"] = L.describe(tab, tab["natr_bp"].to_numpy(float), "CTRL", cn["q"], cn["lower"], nd)
        rows.to_parquet(os.path.join(a.out, f"rows-{win}{'-TEST' if test else ''}-{side}.parquet"))
        res["sides"][side] = sres
    res["gw_ok"] = bool(gw_ok)
    res["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(a.out, f"read-{win}{'-TEST' if test else ''}.json"), "w", encoding="utf-8") as _f:
        json.dump(res, _f, ensure_ascii=False, indent=1, default=_jd)
    print(json.dumps({"win": win, "gw_ok": res["gw_ok"], "rows": {s: v.get("rows_U1") for s, v in res["sides"].items()}}, ensure_ascii=False))


def _jd(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


# ───────────────────────────── объединённое окно ─────────────────────────────

def pooled_rows(out, side):
    parts = []
    for i, w in enumerate(POOL_WINS):
        df = pd.read_parquet(os.path.join(out, f"rows-{w}-{side}.parquet"))
        df = df.copy()
        df["win"] = w
        df["pday"] = i * 31 + (df["day"] - L.WINDOWS[w][0])        # сутки 0…30 = июль, 31…61 = август (подряд идущие календарные сутки)
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def verdicts(df, fs, targets, idx, sigma, ndays=POOL_DAYS):
    """Статистики и вердикты целей стороны на объединённой серии. df — строки обоих окон с `s_<T>`; fs — запись стороны заморозки; idx — индексы бутстрепа."""
    out = {}
    pv, pref = [], []
    boots = {}
    for t in targets:
        q, lower = fs["targets"][t]["q"], fs["targets"][t]["lower"]
        s = df[f"s_{t}"].to_numpy(float)
        sel = L.pick_selected(s, q, lower)
        net = df["net_nf"].to_numpy(float)
        pday = df["pday"].to_numpy(np.int64)
        sa, ca, ss, cs = L.day_series(pday, net, sel, ndays, 0)
        s1b, s2b = L.boot_stats(sa, ca, ss, cs, ndays, idx=idx)
        s0b = L.boot_mean_sel(ss, cs, idx)           # §13.23.3: главная метрика — среднее $ на сделку выбранных; сумма $/сут. — охранная и описательная
        ps = L.point_stats(net, sel, ndays)
        p1, p0, p2 = L.p_one_sided(s1b, "le0"), L.p_one_sided(s0b, "le0"), L.p_one_sided(s2b, "le0")
        pv.append(max(p1, p0))
        pref.append(L.p_one_sided(s1b, "ge0"))
        month = {}
        for w in POOL_WINS:
            mw = (df["win"] == w).to_numpy()
            month[w] = L.point_stats(net[mw], sel[mw], L.WINDOWS[w][2])
        out[t] = dict(ps, p1=p1, p0=p0, p2=p2, p=pv[-1], S0_lo95=float(np.nanquantile(s0b, 0.05)), p_ref=pref[-1], S2_lo95=float(np.nanquantile(s2b, 0.05)), S1_hi95=float(np.nanquantile(s1b, 0.95)),
                      S1_lo95=float(np.nanquantile(s1b, 0.05)), S2_hi95=float(np.nanquantile(s2b, 0.95)),
                      S2_no_top2days=L.drop_top_sum(net, sel, pday, 2) / ndays, S2_no_top2coins=L.drop_top_sum(net, sel, df["sym"].to_numpy(), 2) / ndays,
                      months=month, sel_share=float(sel.mean()),
                      mde_S0_noholm=L.mde_s0(sigma, ps["n_sel"], 1), mde_S0_holm=L.mde_s0(sigma, ps["n_sel"], max(len(targets), 1)),
                      mde_lift_noholm=L.mde_lift(sigma, ps["n_sel"], 1), mde_lift_holm=L.mde_lift(sigma, ps["n_sel"], max(len(targets), 1)))
    rej = L.holm_reject(pv) if pv else []
    rej_ref = L.holm_reject(pref) if pref else []
    for i, t in enumerate(targets):
        r = out[t]
        r["holm_reject"], r["holm_reject_ref"] = bool(rej[i]), bool(rej_ref[i])
        s1_jul, s1_aug = r["months"]["jul"]["S1"], r["months"]["aug"]["S1"]
        r["S1_jul_pos"] = bool(s1_jul > 0 and r["months"]["jul"]["n_sel"] >= L.MIN_SEL)      # §13.23.3: месяц с выбранными < порога — условие не выполнено
        r["S1_aug_pos"] = bool(s1_aug > 0 and r["months"]["aug"]["n_sel"] >= L.MIN_SEL)
        if r["n_sel"] < L.MIN_SEL:
            r["verdict"] = f"не определена (выбранных < {L.MIN_SEL})"
        elif r["holm_reject"] and r["S0_lo95"] > 0 and r["S2"] > 0 and r["S2_no_top2days"] > 0 and r["S2_no_top2coins"] > 0 and r["S1_jul_pos"] and r["S1_aug_pos"]:
            r["verdict"] = "подтверждено"
        elif r["holm_reject_ref"] and r["S1_hi95"] < 0:
            r["verdict"] = "опровергнуто"
        else:
            r["verdict"] = "не ясно"
    return out


def pool(a):
    fr = verify(a.frozen, a.frozen_sha256)
    for w in POOL_WINS:
        if not os.path.exists(os.path.join(a.out, f"READ-{w}.started")):
            raise SystemExit(f"окно {w} ещё не прочитано")
    idx = L.boot_indices(POOL_DAYS)
    res = {"days": POOL_DAYS, "B": L.B_BOOT, "seed": L.SEED, "sides": {}}
    for side in ("long", "short"):
        fs = fr["sides"].get(side)
        if not fs or not fs.get("pass"):
            res["sides"][side] = {"m": 0, "note": "нет целей, прошедших ворота OOF — июль и август по стороне не читались"}
            continue
        df = pooled_rows(a.out, side)
        res["sides"][side] = {"m": fs["m"], "targets": verdicts(df, fs, fs["pass"], idx, fs["sigma_net_U1"]),
                              "control_natr_bp": None, "sigma_net_U1F": fs["sigma_net_U1"], "trades_per_day_U1F": fs["trades_per_day_U1"]}
        cn = fs["control_natr_bp"]
        nb = df["natr_bp"].to_numpy(float)
        sel = L.pick_selected(nb, cn["q"], cn["lower"])
        res["sides"][side]["control_natr_bp"] = L.point_stats(df["net_nf"].to_numpy(float), sel, POOL_DAYS)
    with open(os.path.join(a.out, "pool.json"), "w", encoding="utf-8") as _f:
        json.dump(res, _f, ensure_ascii=False, indent=1, default=_jd)
    for side, sv in res["sides"].items():
        for t, r in sv.get("targets", {}).items():
            print(f"{side} {t}: n_sel={r['n_sel']} S1={r['S1']:.3f} S2={r['S2']:.3f} p={r['p']:.4f} holm={r['holm_reject']} S2lo95={r['S2_lo95']:.3f} "
                  f"S1_jul>0={r['S1_jul_pos']} S1_aug>0={r['S1_aug_pos']} → {r['verdict']}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("read")
    r.add_argument("--win", required=True, choices=["F", "jul", "aug", "hold"])
    r.add_argument("--frozen", required=True)
    r.add_argument("--frozen-sha256", required=True)
    r.add_argument("--models", required=True)
    r.add_argument("--run", required=True)
    r.add_argument("--feat-dir", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--procs", type=int, default=6)
    p = sp.add_parser("pool")
    p.add_argument("--frozen", required=True)
    p.add_argument("--frozen-sha256", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    read_window(a) if a.cmd == "read" else pool(a)


if __name__ == "__main__":
    main()
