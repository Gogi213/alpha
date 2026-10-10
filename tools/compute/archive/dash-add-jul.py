#!/usr/bin/env python3
"""TK-017 (v38 → v39): период «Июль» на дашборде для форм, посчитанных на июле (TK-010, П-07 поправка 5).

Контракт (CEO, TK-017): `account['jul'][key]` — та же схема, что `account['aug']` (`merge.account_summary` строки
ps.json эпохи «июль», span 31 сут); `nh.by_variant[key].kpi07.frac['jul']`, `n_main['jul']`, `max_gap_h_jul`,
`max_gap_censored_jul` — `kpi-newhigh.rolling_kpi(закрытия июля, h = 5 сут)` с границами месяца июля
(`p07-jul-read.patch_july`: весь июль — месяц «aug» модуля); статус июля — вердикт Судьи TK-010 (413da17) из отчёта
`p07-july-2026-09-28.json` (`kpi.state`), доля сверяется с ним. Вариантам без июля ключей нет (страница — «—»).
Счёт и закрытия — выгрузка `dash-jul-dump.py` (portfolio-sim прогонов `p07-jul-read.py`), свои суммы не считаются.

    python tools/compute/dash-add-jul.py --data data/titration-dashboard/data-v38.json \\
        --dump data/titration-dashboard/jul-r1.json --report docs/findings/p07-july-2026-09-28.json \\
        --out data/titration-dashboard/data-v39.json
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


merge, kn, pjr = mod("merge", "titration-dashboard-merge.py"), mod("kn", "kpi-newhigh.py"), mod("pjr", "p07-jul-read.py")
pjr.patch_july(kn)
# форма TK-010 → ключ страницы (формы сверены по `form` выгрузки и строке варианта)
MAP = {"main": "btc4h_trail", "a-base": "g85", "b-base": "g85b", "b-k1": "p07b:h9r-k1", "a-fr1": "p07a:h2-fr1"}
PERIOD = {"key": "jul", "label": "Июль", "from": "2026-07-01", "to": "2026-07-31"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = json.load(open(a.data, encoding="utf-8"))
    B = json.load(open(a.dump, encoding="utf-8"))
    R = json.load(open(a.report, encoding="utf-8"))
    if not any(p["key"] == "jul" for p in D["periods"]):
        D["periods"].append(dict(PERIOD))
    D["account"].setdefault("jul", {})
    span = 31 * 1440
    for name, key in MAP.items():
        b, r = B[name], R[name]
        assert b["form"] == r["form"], (name, b["form"], r["form"])
        assert key in D["account"]["aug"] and key in D["nh"]["by_variant"], key
        acc = merge.account_summary(b["grid"], D["deposit_usd"], span)
        assert (acc["n"], acc["net_usd"]) == (r["n_trades"], round(r["usd"]["est"], 2)), (name, acc["n"], acc["net_usd"])
        D["account"]["jul"][key] = acc
        roll = kn.rolling_kpi(sorted(tuple(x) for x in b["closes"]), h_days=5)["aug"]
        frac, n_main = roll["frac_gt_h"], roll["n_main"]
        assert (frac, n_main) == (r["kpi"]["frac_gt_5d"], r["kpi"]["n_main"]), (name, frac, n_main)
        k07 = D["nh"]["by_variant"][key].setdefault("kpi07", {})
        k07.setdefault("frac", {})["jul"] = frac
        k07.setdefault("n_main", {})["jul"] = n_main
        k07["max_gap_h_jul"] = round(roll["max"]["max"], 1)
        k07["max_gap_censored_jul"] = roll["max"]["max_censored"]
        k07.setdefault("states", {})["jul"] = r["kpi"]["state"]
        k07["verdict_jul"] = r["kpi"]["state"]
        k07["verdict_jul_src"] = "TK-010, Судья 413da17 (reviews/P-07-2026-09-28-july.md)"
        print(f"{key:14s} n={acc['n']:4d} ${acc['net_usd']:8.2f} доля={frac} макс={k07['max_gap_h_jul']} ч"
              f"{' (цензура)' if k07['max_gap_censored_jul'] else ''} {r['kpi']['state']}")
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    open(a.out, "w", encoding="utf-8").write(s)
    print(a.out, f"{len(s.encode('utf-8')) / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
