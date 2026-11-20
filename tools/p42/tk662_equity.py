#!/usr/bin/env python3
"""TK-662: вкладка «Эквити» страницы :8090 целиком на 775 контрактах (01.07–13.11, 500 $) — сделки для видов «Кривая» (с компаундингом) и «Форма» и новые ряды «Баз».
Только чтение готовых выходов (движок не запускается, В-213 — только дельта): трейды прогонов, склейка окон `tk604_stats.splice` через `tk638_post.load_cell`
(gross − fees, без фандинга; сутки — по дню входа), R:R и стопы — `tk620_rr.rr_stats` по тем же сделкам.
Линии: базы U2 TK-621 (`S_base`, `L_base`), мейкер G-626c (`S_mk1`, `L_mk2`), лестницы TK-638 / TK-639 (`L_n24`, `NC360_q20`) — как tk645_best.py;
новые «найдено на этих данных»: шорт без выхода rt `B_off_tt20_tb0.1` (TK-643, блок B), шорт `A_n_1` (TK-657), лонг `SHARP` и `DENSE` = `ZV6_le_q20` (TK-654).
Сверка: сделки и Σ == universe.json U2 / принятые res/cells*.json / отчёты TK-626 и TK-641 (S_mk1 +199,77 $ / 272, L_mk2 +180,83 $ / 327); расхождение — остановка.
Запуск на srv: python3 tk662_equity.py <код TK-639 (/data/p42-tk639/code)> <bases.json> <universe.json> <equity.json>   (ml_page_update.sh — после tk601_equity.py и tk645_best.py)
Выход: в equity.json — график charts["775"] {title, start_ms, end_ms, lines: {ключ: {side, src, n, sum, rr, stops_n, stop_mean, stop_mean_pct}}, sides: {short|long: {t, en, net, sym, day, w, sel}}}:
сделки стороны — все линии подряд, по закрытию; w — ключ линии; sel["base"] — база стороны (S_b775 / L_b775), sel[ключ] — сделки остальных линий.
В bases.json дописываются ряды новых линий (cells) и R:R всех линий (rr)."""
import datetime as dt
import json
import os
import sys

import numpy as np

code, bases_path, uni_path, eq_path = sys.argv[1:5]
sys.path.insert(0, code)
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk639_post as p9  # noqa: E402

B = json.load(open(bases_path, encoding="utf-8"))
UNI = json.load(open(uni_path, encoding="utf-8"))
assert B["days"] == st.NDAYS == UNI["days"] and B["d0"] == str(st.D0) == UNI["d0"], "окно bases.json / universe.json / tk604_stats разное"
u = r5.Uni("h775")
W = [w.name for w in st.TRADE_WINDOWS]


def ref_json(path, cid, group="cells"):
    c = json.load(open(path, encoding="utf-8"))[group][cid]
    return (c["n"], c["sum"]) if "sum" in c else (c["n"], c["sigma"])


def uni_ref(side):
    t = UNI["sides"][side]["U2"]["rows"][-1]
    return t["trades"], t["sum"]


GMK = "/data/p42-tk626/gmk/out"
# ключ: (сторона, каталог окна, (сделок, Σ) эталона, допуск Σ, новый ряд для bases.json)
CELLS = {
    "S_b775": ("S", lambda w: "/data/p42-tk621/out/all_t24/%s/S_base" % w, uni_ref("short"), 1e-6, False),
    "S_mk775": ("S", lambda w: os.path.join(GMK, w, "cd0838447e287"), (272, 199.77), 0.006, False),
    "S_nc360": ("S", lambda w: p9.kind_dir(u, "ladd39", w, "S", "NC360_q20"), ref_json("/data/p42-tk639/res/cells39-h775-S.json", "NC360_q20", "ladders"), 1e-6, False),
    "S_rf0": ("S", lambda w: "/data/p42-tk643/cells/%s-S/B_off_tt20_tb0.1" % w, ref_json("/data/p42-tk643/res/cells43-S.json", "B_off_tt20_tb0.1"), 1e-6, True),
    "S_an1": ("S", lambda w: "/data/p42-tk657/cells/%s-S/A_n_1" % w, ref_json("/data/p42-tk657/res/cells57-S.json", "A_n_1"), 1e-6, True),
    "L_b775": ("L", lambda w: "/data/p42-tk621/out/all_t24/%s/L_base" % w, uni_ref("long"), 1e-6, False),
    "L_n24": ("L", lambda w: p8.kind_dir(u, "ladd", w, "L", "N24_q60"), ref_json("/data/p42-tk638/res/cells38-h775-L.json", "N24_q60", "ladders"), 1e-6, False),
    "L_mk2": ("L", lambda w: os.path.join(GMK, w, "cf280a1e8fc7c"), (327, 180.83), 0.006, False),
    "L_sharp": ("L", lambda w: "/data/p42-tk654/cells54/%s-L/SHARP" % w, ref_json("/data/p42-tk654/res/cells54-L.json", "SHARP"), 1e-6, True),
    "L_dense": ("L", lambda w: "/data/p42-tk654/cells54/%s-L/ZV6_le_q20" % w, ref_json("/data/p42-tk654/res/cells54-L.json", "ZV6_le_q20"), 1e-6, True),
}


def load_cell_window_g7(side, dirs):
    """Как tk643_run.load_cell (отступление G7 §13.43, его же берёт TK-657): у клеток без выхода `rt` сделки держатся до 3,4 сут., поэтому Σ daily = Σ net — по всему окну, не по срезу."""
    tables, daily = p8.read_tables(dirs)
    for w in st.TRADE_WINDOWS:
        tb, dl = tables[w.name], daily[w.name]
        assert abs(float(sum(dl.values())) - float(tb.net.sum())) <= 1e-4 + 2e-6 * len(tb.net), ("G7 окна", w.name, side)
    ser = st.splice(tables, u.coins(), daily=None)
    kinds, pnl = p8.kinds_pnl(tables)
    assert len(pnl) == len(ser.val) and np.allclose(pnl, ser.val, atol=1e-6), side
    return ser, kinds, pnl


WINDOW_G7 = {"S_rf0", "S_an1"}


def stats(S, N):
    S, N = np.asarray(S, float), np.asarray(N, float)
    sd = float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    cum = np.cumsum(S)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    n = int(N.sum())
    return {"days": len(S), "sum": float(S.sum()), "trades": n, "mean": float(S.sum() / n) if n else 0.0,
            "sharpe": float(S.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0, "maxdd": float((peak - cum).max()), "days_pos": int((S > 0).sum()), "days_with_trades": int((N > 0).sum())}


lines, trades = {}, {"S": [], "L": []}
B.setdefault("rr", {})
for k, (side, d, (n_ref, s_ref), tol, new) in CELLS.items():
    ser, kinds, pnl = (load_cell_window_g7 if k in WINDOW_G7 else p8.load_cell)(*(() if k in WINDOW_G7 else (u,)), side, {w: (d(w), side) for w in W})
    S = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
    N = np.bincount(ser.day, minlength=st.NDAYS)
    assert int(N.sum()) == n_ref and abs(float(S.sum()) - s_ref) < tol, (k, float(S.sum()), s_ref, int(N.sum()), n_ref)
    if not new:   # ряд уже в bases.json (tk645_best.py) — тот же
        assert np.allclose(B["cells"][k]["daily_sum"], S, atol=1e-5) and B["cells"][k]["daily_n"] == [int(x) for x in N], k
    else:
        starts = np.concatenate([[0], np.cumsum(B["month_len"])])
        rows = [{"month": B["month_names"][m], **stats(S[starts[m]:starts[m + 1]], N[starts[m]:starts[m + 1]])} for m in range(len(B["month_len"]))]
        rows.append({"month": "всего", **stats(S, N)})
        B["cells"][k] = {"src": d("<окно>"), "rows": rows, "daily_sum": [round(float(x), 6) for x in S], "daily_n": [int(x) for x in N]}
    r = rr.rr_stats([dict(pnl=p, kind=q) for p, q in zip(pnl, kinds)])
    s = r["stops"]
    one = {"rr": r["rr"], "stops_n": s["n"], "stop_mean": s["mean_loss"], "stop_mean_pct": s["mean_loss_pct"]}
    B["rr"][k] = one
    lines[k] = {"side": side, "src": d("<окно>"), "n": int(N.sum()), "sum": float(S.sum()), **one}
    trades[side] += [(int(ser.t_out[i]), int(ser.t_in[i]), round(float(ser.val[i]), 6), ser.sym_name[i], int(ser.day[i]), k) for i in range(len(ser.val))]
    print("%-8s %4d сд., Σ %+9.2f $, R:R %.2f, стопов %d · %+.2f $ (%.1f %% маржи)  %s" % (k, int(N.sum()), float(S.sum()), r["rr"], s["n"], s["mean_loss"], s["mean_loss_pct"], d("<окно>")))

D0_MS = int(dt.datetime.combine(st.D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
BASE_KEY = {"S": "S_b775", "L": "L_b775"}
chart = {"title": "136 суток · 775 контрактов", "start_ms": D0_MS, "end_ms": max(x[0] for v in trades.values() for x in v), "lines": lines, "sides": {}}
for side, name in (("S", "short"), ("L", "long")):
    rows = sorted(trades[side], key=lambda x: (x[0], x[1], x[3], x[5]))
    assert max(x[1] for x in rows) < st.LAST_MS and min(x[1] for x in rows) >= D0_MS, "вход вне 01.07–13.11"
    keys = [BASE_KEY[side]] + [k for k in CELLS if CELLS[k][0] == side and k != BASE_KEY[side]]
    sel = {("base" if k == BASE_KEY[side] else k): [i for i, x in enumerate(rows) if x[5] == k] for k in keys}
    chart["sides"][name] = {"t": [x[0] for x in rows], "en": [x[1] for x in rows], "net": [x[2] for x in rows], "sym": [x[3] for x in rows], "day": [x[4] for x in rows],
                            "w": [x[5] for x in rows], "sel": sel}
E = json.load(open(eq_path, encoding="utf-8"))
E["charts"]["775"] = chart
nan2none = lambda o: {k: nan2none(v) for k, v in o.items()} if isinstance(o, dict) else (None if isinstance(o, float) and o != o else o)   # noqa: E731
B["rr"] = nan2none(B["rr"])
E["charts"]["775"]["lines"] = nan2none(lines)
json.dump(E, open(eq_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
json.dump(B, open(bases_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, allow_nan=False)
