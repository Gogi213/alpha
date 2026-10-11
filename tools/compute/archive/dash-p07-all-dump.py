#!/usr/bin/env python3
"""TK-007 (v33): все посчитанные клетки П-07 (Г-85а/Г-85б: `read-a-*`, `read-b-*`, `h9-a-*`, `h9-b-*`) вариантами
дашборда — обобщение `dash-p07-base-dump.py`: счёт = строки ps.json август/сентябрь (одна строка на эпоху, потолок
как в прогоне), закрытия = ps-closes `[имя][эпоха][str(max_pos)]`, сделки — `ps.load_run` + одна позиция на монету
(только при max_pos == 0 и если есть aug/hist/rec; сверка n сделок = счёт = закрытия, иначе сделок нет + пометка).

    python3 dash-p07-all-dump.py > ~/alpha/tmp-dash/p07-all.json      # на Steam Deck

Выход: {"<каталог>": {"form", "set", "max_pos", "grid", "closes": {aug, sep}, "trades": {aug, sep} | null,
"trades_note"}}. Каталог без ps.json не выгружается (на странице — «нет расчёта»).
"""
import datetime as dt
import glob
import importlib.util
import json
import os

A = os.path.expanduser("~/alpha")
spec = importlib.util.spec_from_file_location("ps", os.path.join(A, "tmp-kpi/portfolio-sim.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
TAGS = {"aug": ["aug"], "sep": ["hist", "rec"]}
RU = {"aug": "август", "sep": "сентябрь"}

out = {}
dirs = sorted(glob.glob(os.path.join(A, "tmp-p07/read-[ab]-*")) + glob.glob(os.path.join(A, "tmp-p07/h9-[ab]-*")))
for d in dirs:
    if not os.path.isfile(os.path.join(d, "ps.json")):
        continue
    name = os.path.basename(d)
    P = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))
    co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8"))
    v = P["variants"][0]
    set_name, form, vn = v["set"], v["form"], v["name"]
    grid = [g for g in P["grid"] if g["epoch"] in RU.values()]
    mp = {g["max_pos"] for g in grid}
    if len(grid) != 2:  # прогон без строк август/сентябрь (пустой счёт) — на странице «нет расчёта»
        continue
    assert len(mp) == 1, (name, mp)
    mp = mp.pop()
    rec = {"form": form, "set": set_name, "max_pos": mp, "grid": grid, "closes": {}, "trades": {}, "trades_note": None}
    has_runs = all(os.path.isdir(os.path.join(d, t)) for t in ("aug", "hist", "rec"))
    for pk, tags in TAGS.items():
        rec["closes"][pk] = co[vn][RU[pk]][str(mp)]
        n_acc = next(g["n"] for g in grid if g["epoch"] == RU[pk])
        assert n_acc == len(rec["closes"][pk]), (name, pk, n_acc, len(rec["closes"][pk]))
        if not has_runs or mp != 0 or rec["trades"] is None:
            rec["trades"] = None
            rec["trades_note"] = rec["trades_note"] or ("потолок позиций — сделки не выгружаются" if mp else "нет прогонов сделок")
            continue
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
        if len(taken) != n_acc:
            rec["trades"] = None
            rec["trades_note"] = f"сделок {len(taken)} ≠ счёт {n_acc} ({pk}) — сделки не выгружаются"
            continue
        rec["trades"][pk] = taken
    out[name] = rec
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
