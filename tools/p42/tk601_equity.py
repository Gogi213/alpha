"""П-42 TK-601: данные вкладки «Эквити» — сделки баз U1 и «трети» целей TK-598, по времени закрытия.

Только чтение готовых выходов TK-598 (без новых моделей и без пересчёта меток): сделки — trades.csv + detail.jsonl прогонов движка (`out/jul-1`, `out/aug-1`, `out/F-1`),
выбор — сохранённые оценки (`read/rows-*.parquet` для июля и августа, `fit/oof-*.parquet` для окна подбора) и замороженная граница `q` (`freeze/frozen.json`).
Сверка с проверенными числами (`read/read-*.json`, `read/pool.json`, `fit/fit-*.json`): число выбранных и всех, сумма и среднее — любое расхождение > 1e-6 даёт код 1.

Использование: <venv>/bin/python tk601_equity.py <каталог /data/p42-tk598> <выход equity.json>

Выход (эквити.json):
  charts.ja / charts.fit — окно «июль + август (чтение)» и «окно подбора 15.09–13.11»: {title, start_ms, end_ms, split_ms (только ja), sides: {short|long: {t, en, net, sym, day, w, sel: {T: [индексы]}}}}
  t — закрытие сделки (мс UTC), en — вход, net — прибыль без фандинга net_nf ($), sym — монета, day — сутки сигнала от начала окна (июль + август — сквозная серия 0…61), w — окно;
  сделки отсортированы по времени закрытия; sel[T] — индексы сделок, выбранных целью T (лонг — T6; шорт — T1, T2, T4, T5; T4 — нижняя треть).
  recon — {n, bad[], items[]}: что с чем сверено.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

base, out_path = sys.argv[1:3]
sys.path.insert(0, os.path.join(base, "py"))
import tk598_lib as L  # noqa: E402

SIDES = {"short": ("S_u1", ["T1", "T2", "T4", "T5"]), "long": ("L_u1", ["T6"])}
TOL = 1e-6
recon = {"n": 0, "bad": [], "items": []}


def eq(what, got, want):
    recon["n"] += 1
    ok = abs(float(got) - float(want)) <= TOL * max(1.0, abs(float(want)))
    recon["items"].append({"what": what, "got": float(got), "want": float(want), "ok": bool(ok)})
    if not ok:
        recon["bad"].append(what)


def t0_ms(win):
    d = dt.datetime.strptime(L.WINDOWS[win][3], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def window_trades(run, side, win):
    """Сделки U1 окна: сутки сигнала в границах окна; время — абсолютное (T0 окна + мс движка)."""
    variant = SIDES[side][0]
    d = os.path.join(base, "out", run, "eng", variant)
    tr = L.add_labels(L.trade_frame(os.path.join(d, f"{side}-trades.csv"), os.path.join(d, f"{side}-detail.jsonl")))
    tr = tr[L.window_mask(tr["day"], win)].reset_index(drop=True)
    o = t0_ms(win)
    tr["t"] = o + tr["exit_ms"].astype("int64")
    tr["en"] = o + tr["entry_ms"].astype("int64")
    tr["w"] = win
    return tr


def attach_scores(tr, scores, targets):
    cols = L.KEY + [f"s_{t}" for t in targets]
    sc = scores[cols].copy()
    sc["level"] = sc["level"].astype(np.int64)
    sc["sig_ms"] = sc["sig_ms"].astype(np.int64)
    out = tr.merge(sc, on=L.KEY, how="left", validate="one_to_one")
    if out[f"s_{targets[0]}"].isna().any():
        raise SystemExit("сделки без сохранённой оценки")
    return out


def selections(tr, frozen_side, targets):
    sel = {}
    for t in targets:
        td = frozen_side["targets"][t]
        sel[t] = L.pick_selected(tr[f"s_{t}"].to_numpy(float), td["q"], td["lower"])
    return sel


def pack(tr, sel, day_offset):
    order = np.lexsort((tr["sym"].to_numpy(), tr["en"].to_numpy(), tr["t"].to_numpy()))
    tr = tr.iloc[order].reset_index(drop=True)
    pos = {t: np.flatnonzero(sel[t][order]).tolist() for t in sel}
    lo = L.WINDOWS[tr["w"].iloc[0]][0]
    return tr, {"t": tr["t"].astype("int64").tolist(), "en": tr["en"].astype("int64").tolist(), "net": [float(v) for v in tr["net_nf"]], "sym": tr["sym"].tolist(),
                "day": (tr["day"].astype("int64") - lo + tr["w"].map(day_offset)).tolist(), "w": tr["w"].tolist(), "sel": pos}


def top2_days_drop(net, day, sel):
    g = pd.Series(net[sel]).groupby(day[sel]).sum().sort_values(ascending=False)
    return float(net[sel].sum() - g.iloc[:2].sum())


frozen = json.load(open(os.path.join(base, "freeze", "frozen.json")))
pool = json.load(open(os.path.join(base, "read", "pool.json")))
charts = {"ja": {"title": "Июль + август 2026 (окна чтения)", "sides": {}}, "fit": {"title": "Окно подбора 15.09–13.11 (60 суток, выбор по внефолдовым оценкам)", "sides": {}}}

for side, (variant, targets) in SIDES.items():
    fs = frozen["sides"][side]
    assert sorted(fs["pass"]) == sorted(targets), (side, fs["pass"])
    # ── июль + август: одна серия
    parts, sels = [], []
    for win, run in (("jul", "jul-1"), ("aug", "aug-1")):
        tr = window_trades(run, side, win)
        rows = pd.read_parquet(os.path.join(base, "read", f"rows-{win}-{side}.parquet"))
        tr = attach_scores(tr, rows, targets)
        s = selections(tr, fs, targets)
        rd = json.load(open(os.path.join(base, "read", f"read-{win}.json")))["sides"][side]["targets"]
        for t in targets:
            eq(f"{win} {side} {t} выбрано", s[t].sum(), rd[t]["n_sel"])
            eq(f"{win} {side} {t} среднее выбранных", tr["net_nf"].to_numpy()[s[t]].mean(), rd[t]["mean_sel"])
            eq(f"{win} {side} {t} всего сделок", len(tr), rd[t]["n_all"])
            eq(f"{win} {side} {t} среднее всех", tr["net_nf"].mean(), rd[t]["mean_all"])
        parts.append(tr)
        sels.append(s)
    sel = {t: np.concatenate([s[t] for s in sels]) for t in targets}
    # сортировка и упаковка: sel переупорядочивается вместе со сделками
    tr_all = pd.concat(parts, ignore_index=True)
    _, pk = pack(tr_all, sel, {"jul": 0, "aug": 31})
    net = tr_all["net_nf"].to_numpy(float)
    pd_ = np.asarray((tr_all["day"].astype("int64") - 14 + tr_all["w"].map({"jul": 0, "aug": 31})).tolist())
    ps = pool["sides"][side]["targets"]
    for t in targets:
        eq(f"ja {side} {t} выбрано", sel[t].sum(), ps[t]["n_sel"])
        eq(f"ja {side} {t} всего сделок", len(tr_all), ps[t]["n_all"])
        eq(f"ja {side} {t} среднее выбранных", net[sel[t]].mean(), ps[t]["mean_sel"])
        eq(f"ja {side} {t} среднее всех", net.mean(), ps[t]["mean_all"])
        eq(f"ja {side} {t} сумма выбранных / 62 сут. (S2)", net[sel[t]].sum() / pool["days"], ps[t]["S2"])
        eq(f"ja {side} {t} сумма всех / 62 сут. (S3)", net.sum() / pool["days"], ps[t]["S3"])
        eq(f"ja {side} {t} сумма выбранных без 2 лучших суток / 62 (S2_no_top2days)", top2_days_drop(net, pd_, sel[t]) / pool["days"], ps[t]["S2_no_top2days"])
    charts["ja"]["sides"][side] = pk
    # ── окно подбора: внефолдовые оценки
    trf = window_trades("F-1", side, "F")
    oof = pd.read_parquet(os.path.join(base, "fit", f"oof-{side}.parquet"))
    trf = attach_scores(trf, oof, targets)
    self_ = selections(trf, fs, targets)
    fj = json.load(open(os.path.join(base, "fit", f"fit-{side}.json")))["targets"]
    netf = trf["net_nf"].to_numpy(float)
    for t in targets:
        o = fj[t]["oof"]
        eq(f"fit {side} {t} выбрано", self_[t].sum(), o["n_sel"])
        eq(f"fit {side} {t} всего сделок", len(trf), o["n_all"])
        eq(f"fit {side} {t} среднее выбранных", netf[self_[t]].mean(), o["mean_sel"])
        eq(f"fit {side} {t} среднее всех", netf.mean(), o["mean_all"])
    _, pkf = pack(trf, self_, {"F": 0})
    charts["fit"]["sides"][side] = pkf

charts["ja"]["start_ms"] = t0_ms("jul") + 14 * L.DAY
charts["fit"]["start_ms"] = t0_ms("F") + 14 * L.DAY
charts["ja"]["split_ms"] = t0_ms("aug") + 14 * L.DAY   # 01.08: граница июль | август
for k, c in charts.items():
    c["end_ms"] = max(max(s["t"]) for s in c["sides"].values())
doc = {"meta": {"note": "net_nf = gross − fees (без фандинга); время — закрытие сделки, UTC", "frozen_sha256": "d2d189d787779ad67ff815e5ffd30106d668df72671e356d14ee742a05d95a59"}, "charts": charts, "recon": recon}
json.dump(doc, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, allow_nan=False)
print(f"сверок {recon['n']}, расхождений {len(recon['bad'])}", *recon["bad"][:10], sep="\n")
print({k: {s: len(v["t"]) for s, v in c["sides"].items()} for k, c in charts.items()})
sys.exit(1 if recon["bad"] else 0)
