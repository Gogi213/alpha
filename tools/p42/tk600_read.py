"""П-42 §13.25 п. 6 (TK-600), этап 2: чтение окон июль / август ЗАМОРОЖЕННЫМИ моделями обеих рук (X и f12); подтверждение — на объединённом окне 01.07–31.08 (62 суток).

  python3 tk600_read.py read --win jul|aug --frozen <frozen600.json> --frozen-sha256 <hex> --models-x <каталог> --models-f12 <каталог> --run <out/NAME> --feat-dir <каталог feat>
                             --out <каталог чтения> --t598-read-dir <каталог с READ-jul.started / READ-aug.started TK-598>
      Одно чтение окна: требует файл заморозки, его sha256 (из коммита заморозки), sha256 кода, входов и моделей; требует, чтобы `READ-jul.started` и `READ-aug.started` TK-598 уже существовали
      (проверяется только наличие файлов); создаёт `READ600-<окно>.started` (O_EXCL), повтор отказывает. Строит таблицу строк окна (сделки U1 + метки), ворота G-W (даты, сумма `net` = сумма суточного
      среза движка; печатаются булевы итоги и |Δ|), `ŝ` каждой руки = среднее пяти фолдовых моделей, сохраняет строки окна (`rows-<окно>-<сторона>.parquet`) для объединения.
      `--win F` — только проверка кода на открытых данных F (маркер `READ600-F-TEST.started`, проверка TK-598 не нужна, вердиктов нет).
  python3 tk600_read.py pool --frozen <frozen600.json> --frozen-sha256 <hex> --out <каталог чтения>
      Объединённое окно (62 суток, одна серия): `D = S0_X − S0_f12` на одних строках, замороженные границы `q` каждой руки; блочный бутстреп 4 суток, 20 000, seed 600, одни индексы суток на
      обе руки; p = P(D* ≤ 0); Холм по целям этапа 2 стороны (семьи E2-L, E2-S). «Подтверждено»: Холм отвергает `D ≤ 0`; D > 0 отдельно в июле и августе при выбранных ≥ 30 у обеих рук в месяце;
      D > 0 без 2 лучших суток и без 2 лучших монет. «Опровергнуто» — верхняя односторонняя 95 %-граница D < 0. Остальное — «не ясно». Метки заново не открываются.
Медианы стратегии не печатаются.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk598_table as TB  # noqa: E402
import tk600_compare as C6  # noqa: E402
import tk600_feat as F6  # noqa: E402
from tk598_fit import SPECS, predict, sha256  # noqa: E402

SEED, B, BLOCK = 600, 20_000, 4
POOL_WINS = ("jul", "aug")
POOL_DAYS = 62
ARMS = ("X", "f12")
FEATS_OF = {"X": list(F6.X_FEATS), "f12": list(F6.F12)}


def load_models(frozen, dirs):
    """{(arm, side, sub): (kind, [5 фолдовых моделей])} целей этапа 2 после проверки sha256 каждого файла."""
    from catboost import CatBoostClassifier, CatBoostRanker, CatBoostRegressor
    cls = {"rmse": CatBoostRegressor, "logloss": CatBoostClassifier, "wlogloss": CatBoostClassifier, "rank": CatBoostRanker}
    out = {}
    for side, fs in frozen["sides"].items():
        for arm in ARMS:
            for t in fs["targets"]:
                for sub, sd in fs["arms"][arm][t]["subs"].items():
                    if (arm, side, sub) in out:
                        continue
                    ms = []
                    for f in sd["folds"]:
                        p = os.path.join(dirs[arm], f["file"])
                        if sha256(p) != f["sha256"]:
                            raise SystemExit(f"sha256 модели не совпал: {arm} {f['file']}")
                        m = cls[sd["kind"]]()
                        m.load_model(p)
                        ms.append(m)
                    out[(arm, side, sub)] = (sd["kind"], ms)
    return out


def score(models, arm, side, t, X):
    """ŝ цели: среднее пяти фолдовых моделей; T5 — ŝ = MFE^ − MAE^ (как tk598_read)."""
    def one(sub):
        kind, ms = models[(arm, side, sub)]
        return np.mean([predict(m, kind, X) for m in ms], axis=0)
    return one("mfe") - one("mae") if t == "T5" else one(SPECS[t][0][0])


def verify(frozen_path, frozen_sha):
    if not os.path.exists(frozen_path):
        raise SystemExit("нет файла заморозки: чтение запрещено (G-S)")
    if sha256(frozen_path) != frozen_sha:
        raise SystemExit("sha256 файла заморозки не совпал с коммитом")
    with open(frozen_path, encoding="utf-8") as f:
        fr = json.load(f)
    here = os.path.dirname(os.path.abspath(__file__))
    for fn, h in fr["code_sha256"].items():
        if sha256(os.path.join(here, fn)) != h:
            raise SystemExit(f"код изменён после заморозки: {fn}")
    return fr


def check_inputs(fr, feat_dir):
    """sha256 файлов признаков окон, записанных при заморозке (признаки июля и августа без меток)."""
    for fn, h in fr.get("inputs_sha256", {}).items():
        p = os.path.join(feat_dir, fn)
        if not os.path.exists(p) or sha256(p) != h:
            raise SystemExit(f"вход изменён или отсутствует после заморозки: {fn}")


def check_t598_read(d):
    """§13.25 п. 6: чтение — после того, как READ-jul и READ-aug TK-598 существуют (проверяется только наличие маркеров)."""
    for w in POOL_WINS:
        if not d or not os.path.exists(os.path.join(d, f"READ-{w}.started")):
            raise SystemExit(f"READ-{w}.started TK-598 не найден: чтение запрещено (§13.25 п. 6)")


def claim_marker(out, win):
    """Маркер одного чтения (O_EXCL); повтор — отказ."""
    os.makedirs(out, exist_ok=True)
    marker = os.path.join(out, f"READ600-{win}{'-TEST' if win == 'F' else ''}.started")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"{marker} уже есть — повторное чтение запрещено")
    os.write(fd, (dt.datetime.now(dt.timezone.utc).isoformat() + "\n").encode())
    os.close(fd)
    return marker


def _first_line(path):
    with open(path) as f:
        return f.read().strip()


def _jd(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def read_window(a):
    win = a.win
    test = win == "F"
    fr = verify(a.frozen, a.frozen_sha256)
    check_inputs(fr, a.feat_dir)
    if not test:
        check_t598_read(a.t598_read_dir)
    marker = claim_marker(a.out, win)
    models = load_models(fr, {"X": a.models_x, "f12": a.models_f12})
    feats_saved, L.FEATS = L.FEATS, list(F6.X_FEATS)  # таблица со всеми 55 признаками; рука f12 берёт свои 12 столбцов
    try:
        _read_sides(a, fr, models, marker, win, test)
    finally:
        L.FEATS = feats_saved


def _read_sides(a, fr, models, marker, win, test):
    lo, hi, nd, _t0 = L.WINDOWS[win]
    res = {"win": win, "started_utc": _first_line(marker), "days": [lo, hi], "ndays_calendar": nd, "sides": {}}
    gw_ok = True
    for side, sd in (("long", "L"), ("short", "S")):
        fs = fr["sides"].get(side)
        if not fs or not fs.get("targets"):
            res["sides"][side] = {"skipped": "нет целей этапа 2"}
            continue
        variant = f"{sd}_u1"
        feat = os.path.join(a.feat_dir, f"feat-X-{os.path.basename(a.run.rstrip('/'))}-{variant}.parquet")
        tab, info = TB.build_table(a.run, variant, win, feat, None)
        daily = pd.read_csv(os.path.join(a.run, "eng", variant, f"{side}-daily.csv"))
        raw = pd.read_csv(os.path.join(a.run, "eng", variant, f"{side}-trades.csv"), usecols=["net"])
        gw = {"rows": int(len(tab)), "dropped_outside_window": info["dropped_outside_window"], "distinct_sig_days": info["distinct_sig_days"],
              "sum_net_vs_daily_abs_diff": float(abs(raw["net"].sum() - float(daily["pnl"].sum())))}
        gw["ok"] = bool(gw["dropped_outside_window"] == 0 and gw["sum_net_vs_daily_abs_diff"] <= 0.01)
        gw_ok &= gw["ok"]
        sres = {"G-W": gw, "rows_U1": int(len(tab)), "nan_share_X": {c: float(tab[c].isna().mean()) for c in F6.X_FEATS}, "targets": {}}
        rows = tab[L.KEY + ["day", "net_nf", "y_win", "y_stop", "bps"]].copy()
        net = tab["net_nf"].to_numpy(float)
        for t in fs["targets"]:
            r = {}
            sel = {}
            for arm in ARMS:
                s = score(models, arm, side, t, tab[FEATS_OF[arm]].to_numpy(np.float64))
                rows[f"s{'X' if arm == 'X' else 'F'}_{t}"] = s
                td = fs["arms"][arm][t]
                sel[arm] = L.pick_selected(s, td["q"], td["lower"])
                r[arm] = L.point_stats(net, sel[arm], nd)
            r["D_point"] = r["X"]["mean_sel"] - r["f12"]["mean_sel"]
            sres["targets"][t] = r
        rows.to_parquet(os.path.join(a.out, f"rows-{win}{'-TEST' if test else ''}-{side}.parquet"))
        res["sides"][side] = sres
    res["gw_ok"] = bool(gw_ok)
    res["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(a.out, f"read-{win}{'-TEST' if test else ''}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_jd)
    print(json.dumps({"win": win, "gw_ok": res["gw_ok"], "rows": {s: v.get("rows_U1") for s, v in res["sides"].items()}}, ensure_ascii=False))


# ───────────────────────────── объединённое окно ─────────────────────────────

def pooled_rows(out, side):
    parts = []
    for i, w in enumerate(POOL_WINS):
        df = pd.read_parquet(os.path.join(out, f"rows-{w}-{side}.parquet")).copy()
        df["win"] = w
        df["pday"] = i * 31 + (df["day"] - L.WINDOWS[w][0])        # сутки 0…30 — июль, 31…61 — август (подряд идущие календарные сутки)
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def verdicts_pair(df, fs, targets, idx, ndays=POOL_DAYS):
    """Статистики и вердикты целей стороны на объединённой серии. df — строки обоих окон с `sX_<T>`, `sF_<T>`; fs — запись стороны заморозки; idx — индексы суток бутстрепа."""
    net = df["net_nf"].to_numpy(float)
    pday = df["pday"].to_numpy(np.int64)
    syms = df["sym"].to_numpy()
    out, pv = {}, []
    for t in targets:
        lower = fs["arms"]["X"][t]["lower"]
        selx = L.pick_selected(df[f"sX_{t}"].to_numpy(float), fs["arms"]["X"][t]["q"], lower)
        self_ = L.pick_selected(df[f"sF_{t}"].to_numpy(float), fs["arms"]["f12"][t]["q"], fs["arms"]["f12"][t]["lower"])
        _, _, ssx, csx = L.day_series(pday, net, selx, ndays, 0)
        _, _, ssf, csf = L.day_series(pday, net, self_, ndays, 0)
        db = L.boot_mean_sel(ssx, csx, idx) - L.boot_mean_sel(ssf, csf, idx)
        d = C6.mean_sel(net, selx) - C6.mean_sel(net, self_)
        p = L.p_one_sided(db, "le0")
        pv.append(p)
        months = {}
        for w in POOL_WINS:
            m = (df["win"] == w).to_numpy()
            months[w] = {"n_sel_X": int(selx[m].sum()), "n_sel_f12": int(self_[m].sum()), "D": C6.mean_sel(net[m], selx[m]) - C6.mean_sel(net[m], self_[m]),
                         "S0_X": C6.mean_sel(net[m], selx[m]), "S0_f12": C6.mean_sel(net[m], self_[m])}
        dd, dtop = C6.drop_top_d(net, selx, self_, pday)
        dc, ctop = C6.drop_top_d(net, selx, self_, syms)
        se = float(np.nanstd(db, ddof=1))
        r = {"q_X": fs["arms"]["X"][t]["q"], "q_f12": fs["arms"]["f12"][t]["q"], "lower": lower,
             "n_sel_X": int(selx.sum()), "n_sel_f12": int(self_.sum()), "n_all": int(len(net)), "mean_all": float(net.mean()),
             "S0_X": C6.mean_sel(net, selx), "S0_f12": C6.mean_sel(net, self_), "D": d,
             "sum_sel_X": float(net[selx].sum()), "sum_sel_f12": float(net[self_].sum()),
             "per_day_X": float(net[selx].sum() / ndays), "per_day_f12": float(net[self_].sum() / ndays),
             "p_D": p, "D_lo5": float(np.nanquantile(db, 0.05)), "D_hi95": float(np.nanquantile(db, 0.95)), "SE_D": se, "MDE_D": C6.Z_MDE * se,
             "D_drop2days": dd, "dropped_days": [int(x) for x in dtop], "D_drop2coins": dc, "dropped_coins": dtop, "months": months}
        out[t] = r
    rej = L.holm_reject(pv) if pv else []
    for i, t in enumerate(targets):
        r = out[t]
        r["m"], r["holm_reject"] = len(targets), bool(rej[i])
        mon = r["months"]
        r["D_jul_pos"] = bool(mon["jul"]["D"] > 0 and mon["jul"]["n_sel_X"] >= L.MIN_SEL and mon["jul"]["n_sel_f12"] >= L.MIN_SEL)
        r["D_aug_pos"] = bool(mon["aug"]["D"] > 0 and mon["aug"]["n_sel_X"] >= L.MIN_SEL and mon["aug"]["n_sel_f12"] >= L.MIN_SEL)
        if not (r["D"] == r["D"]) or min(r["n_sel_X"], r["n_sel_f12"]) < L.MIN_SEL:
            r["verdict"] = f"не определена (выбранных < {L.MIN_SEL} или D не определена)"
        elif r["holm_reject"] and r["D_jul_pos"] and r["D_aug_pos"] and r["D_drop2days"] > 0 and r["D_drop2coins"] > 0:
            r["verdict"] = "подтверждено"
        elif r["D_hi95"] < 0:
            r["verdict"] = "опровергнуто"
        else:
            r["verdict"] = "не ясно"
    return out


def pool(a):
    fr = verify(a.frozen, a.frozen_sha256)
    for w in POOL_WINS:
        if not os.path.exists(os.path.join(a.out, f"READ600-{w}.started")):
            raise SystemExit(f"окно {w} ещё не прочитано")
    idx = L.boot_indices(POOL_DAYS, B, BLOCK, SEED)
    res = {"days": POOL_DAYS, "B": B, "seed": SEED, "block_days": BLOCK, "sides": {}}
    for side in ("long", "short"):
        fs = fr["sides"].get(side)
        if not fs or not fs.get("targets"):
            res["sides"][side] = {"m": 0, "note": "нет целей этапа 2 — июль и август по стороне не читались"}
            continue
        res["sides"][side] = {"m": len(fs["targets"]), "targets": verdicts_pair(pooled_rows(a.out, side), fs, fs["targets"], idx)}
    with open(os.path.join(a.out, "pool600.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_jd)
    for side, sv in res["sides"].items():
        for t, r in sv.get("targets", {}).items():
            print(f"{side} {t}: S0_X={r['S0_X']:+.3f} S0_f12={r['S0_f12']:+.3f} D={r['D']:+.3f} p={r['p_D']:.4f} holm={r['holm_reject']} n_sel={r['n_sel_X']}/{r['n_sel_f12']} "
                  f"D_jul>0={r['D_jul_pos']} D_aug>0={r['D_aug_pos']} drop2d={r['D_drop2days']:+.3f} drop2c={r['D_drop2coins']:+.3f} hi95={r['D_hi95']:+.3f} → {r['verdict']}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("read")
    r.add_argument("--win", required=True, choices=["F", "jul", "aug"])
    r.add_argument("--frozen", required=True)
    r.add_argument("--frozen-sha256", required=True)
    r.add_argument("--models-x", required=True)
    r.add_argument("--models-f12", required=True)
    r.add_argument("--run", required=True)
    r.add_argument("--feat-dir", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--t598-read-dir", default="")
    p = sp.add_parser("pool")
    p.add_argument("--frozen", required=True)
    p.add_argument("--frozen-sha256", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    read_window(a) if a.cmd == "read" else pool(a)


if __name__ == "__main__":
    main()
