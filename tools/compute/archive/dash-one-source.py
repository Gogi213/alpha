#!/usr/bin/env python3
"""TK-006 (TK-003): блоки дашборда из одного источника со счётом. Плитки читают `account` (portfolio-sim: одна
позиция на монету, без TRX), а «Причины выхода»/гистограмма/«По монетам» читали `kpi`/`trades` из cases-файла —
все сигналы без «одной позиции» и сентябрь без 14.09. Здесь: сентябрь 4 главных вариантов — сделки, взятые счётом
(`dash-taken-dump.py` на деке), остальным — правило одной позиции (`_lib.one_per_coin`, строго `>`), `augsep` = одна позиция по
aug + sep (счёт — один прогон), `kpi` пересчитан `merge.trade_stats`. Проверка — `dashboard-check.py` (код 0).

    dash-one-source.py --page index-v29.html --taken taken-sep.json --out data-v30.json
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("merge", os.path.join(HERE, "titration-dashboard-merge.py"))
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)
R_SYM, R_T0, R_EXIT = merge.R_SYM, merge.R_T0, merge.R_EXIT


def one_per_coin(rows):
    """То же правило, что `_lib.portfolio.one_per_coin`, на строках страницы (минуты)."""
    taken, busy = [], {}
    for r in sorted(rows, key=lambda x: x[R_T0]):
        if busy.get(r[R_SYM], -1) > r[R_T0]:
            continue
        busy[r[R_SYM]] = r[R_EXIT]
        taken.append(r)
    return taken


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", required=True)
    ap.add_argument("--taken", required=True, help="JSON {период: {вариант: строки}} от dash-taken-dump.py")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.page, encoding="utf-8") as f:
        s = f.read()
    D = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', s, re.S).group(1)
                   .replace("<\\/", "</"))
    with open(a.taken, encoding="utf-8") as f:
        taken = json.load(f)
    T = D["trades"]
    for p in ("aug", "sep", "crash"):
        for v, rows in T.get(p, {}).items():
            T[p][v] = taken[p][v] if v in taken.get(p, {}) else one_per_coin(rows)
    # счёт «август+сентябрь» — один прогон: позиция конца августа занимает монету и в сентябре
    T["augsep"] = {v: one_per_coin(T["aug"].get(v, []) + T["sep"].get(v, [])) for v in T["augsep"]}

    def span(frm, to):
        d0 = dt.datetime.strptime(frm, "%Y-%m-%d")
        d1 = dt.datetime.strptime(to, "%Y-%m-%d") + dt.timedelta(days=1)
        return int((d1 - d0).total_seconds() // 60)
    spans = {p["key"]: span(p["from"], p["to"]) for p in D["periods"]}
    for p in D["kpi"]:
        for v in D["kpi"][p]:
            D["kpi"][p][v] = merge.trade_stats(T[p].get(v) or [], D["position_usd"], D["deposit_usd"], spans[p])
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
    print(a.out, {p: {v: len(r) for v, r in vs.items()} for p, vs in T.items()})


if __name__ == "__main__":
    main()
