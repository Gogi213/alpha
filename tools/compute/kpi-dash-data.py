#!/usr/bin/env python3
"""Данные KPI «до перехая» (В-120) для дашборда: `kpi-newhigh.py --out` → `--merge kpi=<файл>`.

    by_variant  ключ варианта страницы → период (aug/sep/augsep) → распределение (часы) + все периоды (для гистограммы)
    rating      топ-10 рейтинга и главный — строки таблицы «Ровность роста»
    curves      топ-3 и главный → период → [[мс, $ накопительно], …] по закрытиям

    python tools/compute/kpi-dash-data.py --kpi data/kpi/kpi-2026-09-27.json --closes-dir data/kpi \\
        --out data/titration-dashboard/kpi-dash.json
"""
import argparse
import glob
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = {"BTC 4 ч: трейл 1/1": "btc4h_trail", "BTC 4 ч: тейк 1,75 %": "btc4h_take", "Кандидат: трейл 1/1": "cand",
        "Без фильтра просадки": "nofilter", "Г-105 стоп перед стеной": "g105", "Г-85 вход от фронтрана": "g85",
        "Г-86 рыночный вход": "g86", "f-g08": "g08"}
PK = {"aug": "август", "sep": "сентябрь", "augsep": "август+сентябрь"}


def page_key(name):
    if name in PAGE:
        return PAGE[name]
    if name.startswith("П-05 ") and "потолок" not in name:
        return "p05:p05-" + name[5:]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kpi", required=True)
    ap.add_argument("--closes-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("kn", os.path.join(HERE, "kpi-newhigh.py"))
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    K = json.load(open(a.kpi, encoding="utf-8"))
    R, rank, main_name = K["results"], K["rank"], K["main"]
    S = kn.load(a.closes_dir)

    def closes_of(name):
        if name in S:
            return S[name][1]
        if name.startswith("смесь: "):
            parts = name[len("смесь: "):].split(" + ")
            if all(p in S for p in parts):
                return {pk: [(t, p / len(parts)) for n in parts for t, p in S[n][1][pk]] for pk in PK}
        return None

    by_variant = {}
    for name in S:
        key = page_key(name)
        if not key:
            continue
        by_variant[key] = {}
        for pk, (_n, d0, d1) in kn.PERIODS.items():
            ev = sorted(S[name][1][pk])
            hrs, tail, _ = kn.periods_to_high([(t / kn.MS_H, p) for t, p in ev], kn.ms(d0) / kn.MS_H, kn.ms(d1) / kn.MS_H)
            d = kn.dist(hrs, tail)
            by_variant[key][pk] = {**{k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()},
                                   "periods": [round(x, 1) for x in hrs]}
    cols = ("worst", "tail", "tw_p90", "median", "p90", "n")
    row = lambda n: {"name": n, "place": rank.index(n) + 1 if n in rank else None, "group": R[n]["group"], "key": page_key(n),
                     **{pk: {"usd": R[n][pk]["usd"], "n_trades": R[n][pk]["n"], "r2": R[n][pk]["r2"],
                             "plus_weeks": R[n][pk]["plus_weeks"], "episodes": R[n][pk]["episodes"],
                             **{c: R[n][pk]["hours"][c] for c in cols}} for pk in PK}}
    rating = [row(n) for n in rank[:10]] + ([row(main_name)] if main_name not in rank[:10] else [])
    curves = {}
    for n in rank[:3] + [main_name]:
        cl = closes_of(n)
        if cl is None:
            continue
        curves[n] = {}
        for pk in PK:
            c, pts = 0.0, []
            for t, p in sorted(cl[pk]):
                c += p
                pts.append([t, round(c, 2)])
            curves[n][pk] = pts
    out = {"n_rows": len(R), "n_pass": len(rank), "by_variant": by_variant, "rating": rating, "curves": curves,
           "status": "находки на тех же днях подбора; клетки П-05 — на проверке у Судьи"}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: вариантов {len(by_variant)}, строк рейтинга {len(rating)}, кривых {len(curves)}, "
          f"{os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
