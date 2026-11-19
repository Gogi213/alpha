#!/usr/bin/env python3
"""TK-624: PnL на честной вселенной (TK-621) для страницы :8090 — $ и шарп по вселенным U0…U4, лонг и шорт, помесячно, 01.07–13.11, ставка 500 $.
Только чтение выходов /data/p42-tk621/out/<тег>/<окно>/<L_base|S_base>/ (движок a112684a, склейка окон — retitr_full.splice, как у баз: gross − fees по дню входа, без фандинга).
Запуск на srv: python3 tk624_universe.py <каталог с кодом tools/p42> <выход universe.json>
Сверка внутри: суммы и сделки по месяцам == /data/p42-tk621/report.json (принятый отчёт TK-621); расхождение — остановка.
Шарп и просадка — как в tk624_table.py (суточный ряд, дни без сделок = 0, ddof=1, × √365; просадка от пика накопленной прибыли периода)."""
import json
import math
import os
import sys

import numpy as np

code, out_path = sys.argv[1:3]
sys.path.insert(0, code)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402

ROOT = "/data/p42-tk621"
UNIVERSES = [("U0", "gate566", "566 монет, как было"),
             ("U1", "old566_t24", "Те же 566 + оборот на момент сигнала"),
             ("U2", "all_t24", "Честная: все 775 контрактов"),
             ("U3", "crypto_t24", "Только крипто (539)"),
             ("U4", "tradfi_t24", "Только акции и фонды, TradFi (236)")]
SIDES = {"long": ("L_base", "long"), "short": ("S_base", "short")}
MONTHS = ["июль", "август", "сентябрь", "октябрь", "ноябрь (1–13)"]
MKEY = ["jul", "aug", "sep", "oct", "nov"]
_, coins = rr.read_symbols(os.path.join(ROOT, "universe", "symbols-all.txt"))      # 775 ⊇ все вселенные


def stats(S, N):
    S, N = np.asarray(S, float), np.asarray(N, float)
    sd = float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    cum = np.cumsum(S)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    n = int(N.sum())
    return {"days": len(S), "sum": float(S.sum()), "trades": n, "mean": float(S.sum() / n) if n else 0.0,
            "sharpe": float(S.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0, "maxdd": float((peak - cum).max()), "days_pos": int((S > 0).sum()), "days_with_trades": int((N > 0).sum())}


rep = json.load(open(os.path.join(ROOT, "report.json"), encoding="utf-8"))
res = {"d0": str(rf.D0), "stake": 500, "days": rf.NDAYS, "month_len": list(rf.MONTH_LEN), "month_names": MONTHS,
       "universes": [{"id": u, "tag": t, "name": n} for u, t, n in UNIVERSES], "sides": {"long": {}, "short": {}}}
for u, tag, _ in UNIVERSES:
    for side, (variant, pref) in SIDES.items():
        tabs, daily = {}, {}
        for w in rf.WINDOWS:
            d = os.path.join(ROOT, "out", tag, w.name, variant)
            tabs[w.name] = rf.read_trades_csv(os.path.join(d, pref + "-trades.csv"))
            daily[w.name] = rf.read_daily_csv(os.path.join(d, pref + "-daily.csv"))
        S, N = rf.day_sums(rf.splice(tabs, coins, daily=daily))
        rows = []
        for m, n in enumerate(rf.MONTH_LEN):
            d = rf.month_days([m])
            r = stats(S[d], N[d])
            q = rep[tag][side][MKEY[m]]
            assert r["trades"] == q["n"] and abs(r["sum"] - q["sum"]) < 0.006, (tag, side, MKEY[m], r["sum"], q["sum"], r["trades"], q["n"])
            rows.append({"month": MONTHS[m], **r})
        t = stats(S, N)
        q = rep[tag][side]["all"]
        assert t["trades"] == q["n"] and abs(t["sum"] - q["sum"]) < 0.006, (tag, side, "all", t["sum"], q["sum"])
        rows.append({"month": "всего", **t})
        res["sides"][side][u] = {"rows": rows, "daily_sum": [round(float(x), 6) for x in S], "daily_n": [int(x) for x in N]}
        print("%s %-5s %-6s Σ %+8.2f $  %4d сд.  шарп %+5.2f  просадка %7.2f $" % (u, side, tag, t["sum"], t["trades"], t["sharpe"], t["maxdd"]))
json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
