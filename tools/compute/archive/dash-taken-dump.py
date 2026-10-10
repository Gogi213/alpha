#!/usr/bin/env python3
"""TK-006: сделки, взятые счётом (без TRX, одна позиция на монету, без потолка), для блоков дашборда
«Причины выхода» / гистограмма / «По монетам» — из того же источника, что плитки (`portfolio-sim`), а не из
cases-файла (в нём сентябрь без 14.09 — стык e-archive / rec). Источники — как в `t32-dump-trades.py`.

    python3 dash-taken-dump.py > ~/alpha/tmp-dash/taken-sep.json      # на Steam Deck

Строка — формат страницы `trade_rows`: [day, sym, t0_min, exit_min, net_bps, ctrl(None), reason, fill, usd].
"""
import datetime as dt
import importlib.util
import json
import os

A = os.path.expanduser("~/alpha")
spec = importlib.util.spec_from_file_location("ps", os.path.join(A, "tmp-kpi/portfolio-sim.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
VARIANTS = {  # ключ страницы → (набор, форма) — `variants` из protection-dash-notrx-v16.json
    "btc4h_trail": ("t-bid-btc4h-q1", "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"),
    "btc4h_take": ("t-bid-btc4h-q1", "ladder3x2..20w2-pct2-tk1.75-14400-ttl1800"),
    "cand": ("t-bid-btc1h-q1", "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"),
    "nofilter": ("t-bid-age-45", "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"),
}
HOMES = {"sep": [("epochs/e-archive", "b5/titrc-u500r"), ("tmp-t20/p2/rec", "b5/titrc-u500r")]}
out = {}
for period, homes in HOMES.items():
    out[period] = {}
    for vkey, (set_name, form) in VARIANTS.items():
        rows = []
        for h, r in homes:
            rows += [x for x in ps.load_rounds(os.path.join(A, h), r, set_name, form) if x["sym"] != "TRXUSDT"]
        rows.sort(key=lambda x: x["t0"])
        busy, taken = {}, []
        for x in rows:
            if busy.get(x["sym"], 0) > x["t0"]:
                continue
            busy[x["sym"]] = x["t1"]
            day = dt.datetime.fromtimestamp(x["t0"] / 1e9, dt.timezone.utc).strftime("%Y-%m-%d")
            taken.append([day, x["sym"], x["t0"] // 60_000_000_000, x["t1"] // 60_000_000_000, round(x["net"], 2),
                          None, x["reason"], x["fill"], round(x["usd"], 2)])
        out[period][vkey] = taken
print(json.dumps(out, separators=(",", ":")))
