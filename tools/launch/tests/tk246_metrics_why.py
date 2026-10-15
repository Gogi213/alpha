#!/usr/bin/env python3
"""Почему у строки Летописи нет Шарпа/винрейта/перехая (TK-246 п.5): числа по причинам. Читает те же файлы, что страница; бэктест не считает.
  python -I -X utf8 tools/launch/tests/tk246_metrics_why.py"""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import launch

L = launch.letopis()
mets = launch.hash_metrics_file()
routes = launch.load_routes()
why, ex = collections.Counter(), collections.defaultdict(list)
for r in L["rows"]:
    if r["sharpe"] is not None or r["winrate"] is not None:
        why["есть метрики"] += 1
        continue
    mt = mets.get(r["hash"])
    got = {m: p for m, p in zip(L["months"], r["pnl"]) if p is not None}
    if r["status"] == "none":
        k = "нет $ в Летописи (не считали / вне формы)"
    elif not mt or not mt.get("n"):
        k = "есть $, но closes нет (хеш не в metrics.jsonl)" + (f" — маршрут {routes.get(r['hash'], 'cells')}" if routes.get(r["hash"], "cells") != "cells" else "")
    elif set(mt["by_month"]) != set(got):
        k = "closes покрывают другой набор месяцев, чем $ в Летописи"
    elif any(abs(mt["by_month"][m]["pnl"] - v) > 0.02 for m, v in got.items()):
        k = "Σ$ closes расходится с pnl_usd канона (> 0,02 $/мес)"
    else:
        k = "прочее"
    why[k] += 1
    if len(ex[k]) < 3:
        ex[k].append(r["hash"])
print(f"строк {len(L['rows'])}, хешей {L['nhash']}, метрики у {L['metrics_n']}")
for k, n in why.most_common():
    print(f"{n:5d}  {k}  {ex.get(k, '')}")
