#!/usr/bin/env python3
"""TK-624: таблица для владельца — $ и шарп по месяцам для L_base, S_base (TK-599) и S_mk1 (TK-616), 01.07–13.11, ставка 500 $.
Только чтение готовых выходов (ряд — тот же, что у tk616_maker.series_of: gross − fees по дню входа, без фандинга, зачётные диапазоны окон).
Запуск на srv: python3 tk624_table.py <каталог с кодом tools/p42> <выход json>   (выход — data/bases.json страницы :8090; ml_page_update.sh запускает сам)
Шарп = среднее / станд. откл. (ddof=1) суточной прибыли, ВКЛЮЧАЯ дни без сделок, × √365; макс. просадка — по накопленной суточной прибыли периода
(пик → впадина, $, начало периода = 0)."""
import datetime
import json
import math
import os
import sys

import numpy as np

code, out_path = sys.argv[1:3]
sys.path.insert(0, code)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_maker as mk  # noqa: E402

BASE_ROOT, MK_ROOT = mk.BASE_ROOT, mk.ROOT
_, coins = rr.read_symbols(rr.SYMBOLS)
# vid: базы TK-599 — tk616_gate.sh (L_base c53a…, S_base c10f…), S_mk1 — /data/p42-tk616/results/run.json
CELLS = {"L_base": (BASE_ROOT, "c53a116125b39", "L"), "S_base": (BASE_ROOT, "c10fbba7be522", "S"), "S_mk1": (MK_ROOT, "c934346e168dd", "S")}
MONTHS = ["июль", "август", "сентябрь", "октябрь", "ноябрь (1–13)"]


def stats(S, N):
    n = int(N.sum())
    s = float(S.sum())
    sd = float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    sharpe = float(np.mean(S) / sd * math.sqrt(365)) if sd > 0 else 0.0
    cum = np.cumsum(S)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = float((peak - cum).max()) if len(S) else 0.0
    wins = int((S > 0).sum())
    return {"days": len(S), "sum": s, "trades": n, "mean": s / n if n else 0.0, "sharpe": sharpe, "maxdd": dd, "days_pos": wins, "days_with_trades": int((N > 0).sum())}


res = {"d0": str(rf.D0), "stake": 500, "days": rf.NDAYS, "month_len": list(rf.MONTH_LEN), "month_names": MONTHS, "cells": {}}
for name, (root, vid, side) in CELLS.items():
    S, N = mk.series_of(root, vid, side, coins)
    rows = []
    for m, n in enumerate(rf.MONTH_LEN):
        d = rf.month_days([m])
        rows.append({"month": MONTHS[m], **stats(S[d], N[d])})
    rows.append({"month": "всего", **stats(S, N)})
    res["cells"][name] = {"vid": vid, "root": root, "rows": rows, "daily_sum": [round(float(x), 6) for x in S], "daily_n": [int(x) for x in N]}
    t = rows[-1]
    print("%-7s %s: Σ %+.2f $, %d сд., %+.3f $/сд., шарп %.2f, просадка %.2f $" % (name, vid, t["sum"], t["trades"], t["mean"], t["sharpe"], t["maxdd"]))
json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
