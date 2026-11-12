#!/usr/bin/env python3
"""TK-493, T1: таблицы отчёта из CSV переписи (census_tails.py) в markdown. Ничего не считает заново, кроме концентрации по активам (из episodes.npz).
  census_report.py --tables <каталог t1_*.csv> --out <каталог census_tails: episodes.npz, pairs.csv.gz>   > таблицы.md
Только numpy и stdlib."""
import argparse
import csv
import gzip
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import census_tails as C   # noqa: E402


def rd(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def f(x, nd=1):
    return ("%." + str(nd) + "f") % float(x)


def pc(a, b, nd=1):
    a, b = float(a), float(b)
    return "—" if not b else ("%." + str(nd) + "f" + "%%") % (100.0 * a / b)


def i(x):
    return "{:,}".format(int(float(x))).replace(",", " ")


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    def T(n):
        return rd(os.path.join(a.tables, n))
    S = {(r["univ"], r["period"], int(r["theta"])): r for r in T("t1_summary_theta.csv")}
    B = {(r["univ"], r["period"], int(r["theta"])): r for r in T("t1_baseline.csv")}
    ths = (160, 320, 640, 1000)
    pers = ("формирование", "окно", "всего")
    nl = "\n"

    print("### Таблица 1. Объём хвостов и длительность (универсум c1d2: крипта, 5 830 пар активов)" + nl)
    rows = []
    for p in pers:
        for th in ths + (2000,):
            r = S[("c1d2", p, th)]
            n = float(r["episodes"])
            pm = "%.0f" % float(B[("c1d2", p, th)]["per_million"]) if p != "всего" else "—"
            rows.append([p, th, i(r["minutes"]), pm, i(r["episodes"]), i(r["units"]), r["assets"], r["asset_pairs"], r["ex_pairs"], f(r["mx_max"], 0), f(r["D_med"], 0), f(r["D_q90"], 0),
                         pc(r["D_le1"], n, 0), pc(r["D_le2"], n, 0), pc(r["D_le5"], n, 0), pc(r["D_le30"], n, 0)])
    print(table(["период", "θ, bps", "минут", "на 1 млн пар-минут", "эпизодов", "единиц актив×сутки", "активов", "пар активов", "пар бирж из 28", "max |dev|", "D мед, мин", "D q90", "D≤1", "D≤2", "D≤5", "D≤30"], rows))

    print(nl + "### Таблица 2. Сходимость: доля эпизодов с |dev| ≤ θ/4 на минуте t0+h (среди эпизодов, у которых на ней есть сделка у обеих ног; в скобках — их число); «фон» — доля ВСЕХ пар-минут с |dev| ≤ θ/4; «к +h» — первое наблюдение |dev| ≤ θ/4 не позже t0+h (знаменатель — эпизоды с горизонтом до конца данных)" + nl)
    rows = []
    for p in pers:
        for th in ths:
            r = S[("c1d2", p, th)]
            row = [p, th, i(r["episodes"])]
            for h in C.CONV_H:
                row.append("%s (%s)" % (pc(r["conv%d" % h], r["obs%d" % h], 0), i(r["obs%d" % h])))
            row.append("—" if p == "всего" else pc(B[("c1d2", p, th)]["minutes_le_quarter"], B[("c1d2", p, th)]["pair_minutes"], 2))
            for h in (5, 30, 120):
                row.append(pc(r["ever%d" % h], r["den%d" % h], 0))
            rows.append(row)
    print(table(["период", "θ", "эпизодов", "+1 мин", "+5", "+30", "+120", "фон", "≤θ/4 к +5", "к +30", "к +120"], rows))

    print(nl + "### Таблица 3. Протухшая нога перед началом и чем кончается эпизод" + nl)
    rows = []
    for p in pers:
        for th in ths + (2000,):
            r = S[("c1d2", p, th)]
            n = float(r["episodes"])
            rows.append([p, th, i(r["episodes"]), pc(r["stale_any"], n, 0), pc(r["stale_ge5"], n, 0), pc(r["stale_ge30"], n, 0), i(r["units"]), i(r["units_live"]), i(r["units_stale_only"]),
                         pc(r["end_below"], n, 0), pc(r["end_flip"], n, 0), pc(r["end_noobs"], n, 0), pc(r["end_data"], n, 0)])
    print(table(["период", "θ", "эпизодов", "пустая минута на ноге перед началом", "≥5 мин", "≥30 мин", "единиц", "из них с живым началом", "только протухшие", "кончился ниже θ", "обратный знак", "нет сделки на ноге", "конец данных"], rows))

    print(nl + "### Таблица 4. Универсум ext (пары основных контрактов крипты, не вошедшие в c1d2: «нет данных», «пограничное», «разное») — справочно" + nl)
    rows = []
    for p in ("формирование", "окно"):
        for th in ths:
            r = S[("ext", p, th)]
            rows.append([p, th, i(r["minutes"]), i(r["episodes"]), i(r["units"]), r["assets"], f(r["mx_max"], 0), f(r["D_med"], 0), pc(r["D_le5"], float(r["episodes"]), 0), pc(r["stale_any"], float(r["episodes"]), 0)])
    print(table(["период", "θ", "минут", "эпизодов", "единиц", "активов", "max |dev|", "D мед", "D≤5", "пустая минута перед началом"], rows))

    print(nl + "### Таблица 5. Срезы по ноге и по паре бирж (c1d2)" + nl)
    sl = T("t1_slices.csv")
    rows = []
    for p in ("формирование", "окно"):
        for th in (320, 1000):
            for r in sl:
                if r["kind"] == "leg" and r["period"] == p and int(r["theta"]) == th:
                    n = float(r["episodes"])
                    rows.append([p, th, r["slice"], i(r["n_asset_pairs"]), i(r["episodes"]), i(r["units"]), r["assets"], f(r["D_med"], 0), pc(r["D_le5"], n, 0), pc(r["conv5"], r["obs5"], 0), pc(r["stale_any"], n, 0)])
    print(table(["период", "θ", "срез", "пар активов", "эпизодов", "единиц", "активов", "D мед", "D≤5", "сошлось к +5 (по наблюдённым)", "пустая минута перед началом"], rows))
    print(nl + "Пары бирж с наибольшим числом эпизодов θ = 320 в окне:" + nl)
    rr = [r for r in sl if r["kind"] == "ex_pair" and r["period"] == "окно" and int(r["theta"]) == 320]
    rr.sort(key=lambda r: -int(r["episodes"]))
    rows = []
    for r in rr[:10]:
        n = float(r["episodes"])
        r2 = [x for x in sl if x["kind"] == "ex_pair" and x["slice"] == r["slice"] and x["period"] == "окно" and int(x["theta"]) == 1000][0]
        rows.append([r["slice"], i(r["n_asset_pairs"]), i(r["episodes"]), i(r["units"]), r["assets"], pc(r["D_le5"], n, 0), pc(r["conv5"], r["obs5"], 0), pc(r["stale_any"], n, 0), i(r2["episodes"]), i(r2["units"])])
    print(table(["пара бирж", "пар активов", "эпизодов θ=320", "единиц", "активов", "D≤5", "сошлось к +5", "пустая минута перед началом", "эпизодов θ=1000", "единиц θ=1000"], rows))

    print(nl + "### Таблица 6. Концентрация по активам: три актива с наибольшим числом эпизодов" + nl)
    npz = np.load(os.path.join(a.out, "episodes.npz"))
    pairs = list(csv.DictReader(gzip.open(os.path.join(a.out, "pairs.csv.gz"), "rt", encoding="utf-8")))
    key = np.array([p["key"] for p in pairs])
    univ = np.array([p["univ"] for p in pairs])
    rows = []
    for un in ("c1d2", "ext"):
        for p, lo, hi in (("формирование", 0, C.SPLIT), ("окно", C.SPLIT, C.NM)):
            for th in ths:
                E = npz["th%d" % th]
                m = (univ[E["pair"]] == un) & (E["t0"] >= lo) & (E["t0"] < hi)
                ks = key[E["pair"][m]]
                if not len(ks):
                    rows.append([un, p, th, 0, "—", "—"])
                    continue
                u, c = np.unique(ks, return_counts=True)
                o = np.argsort(-c)[:3]
                rows.append([un, p, th, i(len(ks)), ", ".join("%s %s" % (u[j], i(c[j])) for j in o), pc(c[o].sum(), len(ks), 0)])
    print(table(["универсум", "период", "θ", "эпизодов", "активы (эпизодов)", "доля трёх"], rows))

    print(nl + "### Таблица 6б. Единицы «актив×сутки» по суткам (c1d2): число активов с минутой |dev| ≥ θ; восемь суток с наибольшим числом при θ = 320" + nl)
    ud = T("t1_units_by_day.csv")
    ud2 = sorted(ud, key=lambda r: (-int(r["u320"]), -int(r["u160"])))[:8]
    print(table(["сутки", "период", "θ=160", "θ=320", "θ=640", "θ=1000"], [[r["date"], r["period"], r["u160"], r["u320"], r["u640"], r["u1000"]] for r in ud2]))
    print(nl + "Суток с хотя бы одной единицей из 35: θ=160 — %d, θ=320 — %d, θ=640 — %d, θ=1000 — %d." % tuple(sum(1 for r in ud if int(r[k]) > 0) for k in ("u160", "u320", "u640", "u1000")))

    print(nl + "### Таблица 7. Распределение |dev| по парам-минутам (c1d2 + ext; ящики 0,5 / 5 / 50 bps — значения с точностью до ящика)" + nl)
    q = T("t1_absdev_quantiles.csv")
    print(table(["период", "пар-минут", "q50", "q90", "q99", "q99,9", "q99,99", "max по парам"], [[r["period"], i(r["pair_minutes"]), r["q0.5"], r["q0.9"], r["q0.99"], r["q0.999"], r["q0.9999"], f(r["max_pair"], 0)] for r in q]))

    print(nl + "### Таблица 8. Ворота семейства L: единицы «актив×сутки» с |dev| ≥ ecap (формирование / окно / всего)" + nl)
    rows = []
    for r in T("t1_gate_L.csv"):
        rows.append([r["univ"], r["ecap"], i(r["episodes"]), i(r["minutes"]), r["assets"], "%s / %s / %s" % (r["units_form"], r["units_win"], r["units_all"]),
                     "%s / %s / %s" % (r["units_live_form"], r["units_live_win"], r["units_live_all"]), "%s / %s / %s" % (r["units_stale_only_form"], r["units_stale_only_win"], r["units_stale_only_all"]),
                     r["units_live_D2_all"], r["spells_all"], r["max_units_one_asset"], r["top_asset"], r["units_wo_top_asset"], r["gate_ge30"]])
    print(table(["универсум", "ecap", "эпизодов", "минут", "активов", "единиц", "с живым началом", "только протухшие", "живых с D≥2", "серий суток", "макс. у одного актива", "актив", "единиц без него", "≥ 30"], rows))

    print(nl + "### Таблица 9. 20 крупнейших событий (актив×сутки по max|dev| эпизода θ=160; c1d2). dev на +1/+5/+30/+120 — значение на этой минуте от начала эпизода (н/д = нет сделки на ноге); «≤40 через» — минут до первого наблюдения |dev| ≤ 40 bps (θ/4 при θ = 160), −1 = до конца данных не было" + nl)
    top = T("t1_top100_c1d2.csv")[:20]
    rows = []
    for k, r in enumerate(top, 1):
        sg = "A>B" if int(r["sign"]) > 0 else "A<B"
        rows.append([k, r["asset"], "%s/%s %s" % (r["ex_a"], r["ex_b"], sg), "%s %s" % (r["date"], r["t_utc"]), f(r["mx"], 0), r["D"],
                     " / ".join(("н/д" if r[c] == "nan" else f(r[c], 0)) for c in ("d1", "d5", "d30", "d120")), r["fp"], r["end"],
                     "%s / %s" % (r["gap_a"], r["gap_b"]), "%s / %s" % (r["n_pairs"], r["n_ep"]), r["mover"] or "—", r["common_leg"]])
    print(table(["№", "актив", "биржи (A/B) и знак", "начало, UTC", "max |dev|", "D, мин", "dev: +1 / +5 / +30 / +120", "≤40 через", "исход", "пустых минут перед началом A / B", "пар / эпизодов за сутки", "нога, ушедшая дальше от своей цены", "общая биржа пар события"], rows))


if __name__ == "__main__":
    main()
