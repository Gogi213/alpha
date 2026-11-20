#!/usr/bin/env python3
"""TK-645: лучшие варианты лонга и шорта на 775 монетах для вида «Базы» и блока «Где мы» страницы :8090 — дописывает клетки в bases.json.
Только чтение готовых выходов (движок не запускается): L_n24 — лестница TK-638 `ladd-h775/<окно>-L/N24_q60` (заморожен, tk638-frozen.json),
S_nc360 — лестница TK-639 `ladd39-h775/<окно>-S/NC360_q20` (найдено на этих данных); базы той же вселенной (U2, 775 контрактов) — ряды TK-621 из universe.json.
Ряд — как у tk624_table.py: gross − fees по дню входа, без фандинга, склейка окон `tk604_stats.splice` (через tk638_post.load_cell).
Сверка: Σ и сделки == принятые таблицы res/cells38-h775-L.json и res/cells39-h775-S.json; U2 == universe.json (сверен с report.json TK-621); расхождение — остановка.
Запуск на srv: python3 tk645_best.py <каталог с кодом TK-639 (/data/p42-tk639/code)> <bases.json> <universe.json>   (ml_page_update.sh запускает сам после tk624_table.py)"""
import json
import os
import sys

import numpy as np

code, bases_path, uni_path = sys.argv[1:4]
sys.path.insert(0, code)
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk639_post as p9  # noqa: E402

B = json.load(open(bases_path, encoding="utf-8"))
UNI = json.load(open(uni_path, encoding="utf-8"))
assert B["days"] == st.NDAYS == UNI["days"] and B["d0"] == str(st.D0) == UNI["d0"], "окно bases.json / universe.json / tk604_stats разное"
u = r5.Uni("h775")
LADDERS = {"L_n24": ("L", lambda w: p8.kind_dir(u, "ladd", w, "L", "N24_q60"), "/data/p42-tk638/res/cells38-h775-L.json", "N24_q60"),
           "S_nc360": ("S", lambda w: p9.kind_dir(u, "ladd39", w, "S", "NC360_q20"), "/data/p42-tk639/res/cells39-h775-S.json", "NC360_q20")}


def stats(S, N):
    S, N = np.asarray(S, float), np.asarray(N, float)
    sd = float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    cum = np.cumsum(S)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    n = int(N.sum())
    return {"days": len(S), "sum": float(S.sum()), "trades": n, "mean": float(S.sum() / n) if n else 0.0,
            "sharpe": float(S.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0, "maxdd": float((peak - cum).max()), "days_pos": int((S > 0).sum()), "days_with_trades": int((N > 0).sum())}


def put(name, S, N, src):
    starts = np.concatenate([[0], np.cumsum(B["month_len"])])
    rows = [{"month": B["month_names"][m], **stats(S[starts[m]:starts[m + 1]], N[starts[m]:starts[m + 1]])} for m in range(len(B["month_len"]))]
    rows.append({"month": "всего", **stats(S, N)})
    B["cells"][name] = {"src": src, "rows": rows, "daily_sum": [round(float(x), 6) for x in S], "daily_n": [int(x) for x in N]}
    t = rows[-1]
    print("%-8s Σ %+.2f $, %d сд., %+.3f $/сд., шарп %.2f, просадка %.2f $  (%s)" % (name, t["sum"], t["trades"], t["mean"], t["sharpe"], t["maxdd"], src))


for name, (side, d, ref_path, cid) in LADDERS.items():
    ser, _, pnl = p8.load_cell(u, side, {w.name: (d(w.name), side) for w in st.TRADE_WINDOWS})
    S = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
    N = np.bincount(ser.day, minlength=st.NDAYS)
    ref = json.load(open(ref_path, encoding="utf-8"))["ladders"][cid]
    assert int(N.sum()) == ref["n"] and abs(float(S.sum()) - ref["sum"]) < 1e-6, (name, float(S.sum()), ref["sum"], int(N.sum()), ref["n"])
    put(name, S, N, "%s %s" % (ref_path, cid))
for name, side in (("L_b775", "long"), ("S_b775", "short")):
    c = UNI["sides"][side]["U2"]
    put(name, np.array(c["daily_sum"], float), np.array(c["daily_n"], int), "universe.json U2 (TK-621)")
    assert B["cells"][name]["rows"][-1]["trades"] == c["rows"][-1]["trades"] and abs(B["cells"][name]["rows"][-1]["sum"] - c["rows"][-1]["sum"]) < 1e-6, name
json.dump(B, open(bases_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
