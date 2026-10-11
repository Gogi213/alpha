#!/usr/bin/env python3
"""TK-006 (CEO 28.09 00:14): базы П-07 Г-85а / Г-85б вариантами дашборда — счёт, закрытия и сделки из одного прогона
чтения (`p07-read.py`: busy-replay клетки → portfolio-sim без потолка/стопов, без TRX).

    python3 dash-p07-base-dump.py > ~/alpha/tmp-dash/p07-bases.json      # на Steam Deck, после tk004-read.done

Выход: {g85a|g85b: {"form", "grid": [строки ps.json август/сентябрь], "closes": {aug, sep: [[t1_ms, $]]},
"trades": {aug, sep: строки формата страницы}}}; сделки — одна позиция на монету (как счёт), сверка n со счётом.
"""
import datetime as dt
import importlib.util
import json
import os

A = os.path.expanduser("~/alpha")
spec = importlib.util.spec_from_file_location("ps", os.path.join(A, "tmp-kpi/portfolio-sim.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
BASES = {"g85a": "tmp-p07/read-a-base", "g85b": "tmp-p07/read-b-base"}
TAGS = {"aug": ["aug"], "sep": ["hist", "rec"]}
RU = {"aug": "август", "sep": "сентябрь"}

out = {}
for key, d in BASES.items():
    d = os.path.join(A, d)
    P = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))
    co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8"))
    v = P["variants"][0]
    set_name, form = v["set"], v["form"]
    grid = [g for g in P["grid"] if g["epoch"] in RU.values() and g["max_pos"] == 0]
    rec = {"form": form, "set": set_name, "grid": grid, "closes": {}, "trades": {}}
    for pk, tags in TAGS.items():
        rec["closes"][pk] = co["cell"][RU[pk]]["0"]
        rows = []
        for t in tags:
            rows += [x for x in ps.load_run(os.path.join(d, t), ".", set_name, form) if x["sym"] != "TRXUSDT"]
        busy, taken = {}, []
        for x in sorted(rows, key=lambda x: x["t0"]):
            if busy.get(x["sym"], 0) > x["t0"]:
                continue
            busy[x["sym"]] = x["t1"]
            day = dt.datetime.fromtimestamp(x["t0"] / 1e9, dt.timezone.utc).strftime("%Y-%m-%d")
            taken.append([day, x["sym"], x["t0"] // 60_000_000_000, x["t1"] // 60_000_000_000, round(x["net"], 2),
                          None, x["reason"], x["fill"], round(x["usd"], 2)])
        rec["trades"][pk] = taken
        n_acc = next(g["n"] for g in grid if g["epoch"] == RU[pk])
        assert len(taken) == n_acc == len(rec["closes"][pk]), (key, pk, len(taken), n_acc, len(rec["closes"][pk]))
    out[key] = rec
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
