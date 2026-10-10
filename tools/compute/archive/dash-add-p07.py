#!/usr/bin/env python3
"""TK-006 (CEO 28.09 00:14): база Г-85б (П-07, TK-004) вариантом дашборда — счёт, сделки, KPI «до перехая» из одного
прогона чтения (`dash-p07-base-dump.py` на деке). База Г-85а = уже бывший на странице вариант `g85` (сверяется: счёт
август/сентябрь совпадает до цента) — отдельным вариантом не дублируется.

    dash-add-p07.py --data data-v30.json --bases p07-bases.json --out data-v31.json
"""
import argparse
import datetime as dt
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def mod(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


merge, kd, kn = mod("merge", "titration-dashboard-merge.py"), mod("kd", "kpi-dash-data.py"), mod("kn", "kpi-newhigh.py")
os1 = mod("os1", "dash-one-source.py")
KEY = "g85b"
META = {"key": KEY, "label": "Г-85б: 3 заявки лестницей у стены, ширина в σ монеты (база П-07, форма ladder3x0..0.0409sw2, TK-004), остальное как у главного",
        "pill": "Г-85б · у Судьи", "group": "p02",
        "note": "База П-07 Г-85б на августе и сентябре (TK-004) — на проверке у Судьи; строки H9р/H14/H10 — следующей версией."}
CHANGES = {"вход": "3 лимитные заявки лестницей у стены, ширина — в σ монеты (В-126/127/131), вместо лестницы 2–20 bps"}


def by_variant(closes):
    out = {}
    for pk, (_n, d0, d1) in kn.PERIODS.items():
        ev = sorted(closes[pk])
        hrs, tail, _ = kn.periods_to_high([(t / kn.MS_H, p) for t, p in ev], kn.ms(d0) / kn.MS_H, kn.ms(d1) / kn.MS_H)
        d = kn.dist(hrs, tail)
        out[pk] = {**{k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()}, "periods": [round(x, 1) for x in hrs]}
    dd, rl = kn.drawdown_stats(closes["augsep"]), kn.rolling_kpi(closes["augsep"], raw=True)
    rnd = lambda v: (round(v, 1) if isinstance(v, float) else v)
    for pk in ("aug", "sep"):
        raw = rl[pk].pop("raw_max")
        out[pk]["roll"] = {k: ({kk: rnd(vv) for kk, vv in v.items()} if isinstance(v, dict) else rnd(v)) for k, v in rl[pk].items()}
        out[pk]["dd"] = {k: rnd(v) for k, v in dd[pk].items()}
        E = kd.EDGES
        out[pk]["roll_hist"] = [sum(E[i] <= x / 24 < E[i + 1] for x in raw) for i in range(len(E) - 1)]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--bases", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = json.load(open(a.data, encoding="utf-8"))
    B = json.load(open(a.bases, encoding="utf-8"))
    ru = {"aug": "август", "sep": "сентябрь"}
    grid = lambda b, pk: next(g for g in b["grid"] if g["epoch"] == ru[pk])
    for pk in ru:  # Г-85а == g85 на странице
        g, acc = grid(B["g85a"], pk), D["account"][pk]["g85"]
        assert (g["n"], round(g["total_usd"], 2)) == (acc["n"], acc["net_usd"]), (pk, g["n"], g["total_usd"], acc)
    b = B[KEY]
    spans = {}
    for p in D["periods"]:
        d0 = dt.datetime.strptime(p["from"], "%Y-%m-%d")
        spans[p["key"]] = int(((dt.datetime.strptime(p["to"], "%Y-%m-%d") - d0).days + 1) * 1440)
    pos, dep = D["position_usd"], D["deposit_usd"]
    for pk in ru:
        D["trades"][pk][KEY] = b["trades"][pk]
        D["account"][pk][KEY] = merge.account_summary(grid(b, pk), dep, spans[pk])
        D["kpi"][pk][KEY] = merge.trade_stats(b["trades"][pk], pos, dep, spans[pk])
        D["protections"].setdefault(pk, {})[KEY] = None
    D["trades"]["augsep"][KEY] = os1.one_per_coin(b["trades"]["aug"] + b["trades"]["sep"])
    D["kpi"]["augsep"][KEY] = merge.trade_stats(D["trades"]["augsep"][KEY], pos, dep, spans["augsep"])
    closes = {pk: [tuple(x) for x in b["closes"][pk]] for pk in ru}
    closes["augsep"] = closes["aug"] + closes["sep"]
    D["nh"]["by_variant"][KEY] = by_variant(closes)
    D["variants"] = [v for v in D["variants"] if v["key"] != KEY] + [META]
    D["p02_changes"][KEY] = CHANGES
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(D, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(a.out, {pk: (D["account"][pk][KEY]["n"], D["account"][pk][KEY]["net_usd"]) for pk in ru},
          {pk: D["nh"]["by_variant"][KEY][pk]["roll"].get("frac_gt_h") for pk in ru})


if __name__ == "__main__":
    main()
