#!/usr/bin/env python3
"""TK-018 / В-151 («все гипотезы на все три месяца»): 12 клеток П-08 (`docs/research/P-08-new-code-7.md`, закрыт
Судьёй 2689669: все «стоп», кандидатов нет) — вариантами дашборда с августом, сентябрём и июлем, как варианты П-07.

Свои суммы не считаются. Выгрузка ps.json/ps-closes.json → запись (`grid`, `closes`, `form`, `set`, `max_pos`) — как
`dash-p07-all-dump.py` на деке (строки эпох август/сентябрь, закрытия `[вариант][эпоха][str(max_pos)]`, n счёта =
числу закрытий); сделок нет (прогонов aug/hist/rec локально нет) — `trades = None`, как у заполненных `dash-fill-nocalc`.
Авг/сен — `dash-fill-nocalc.compute` (account_summary, acc_augsep, by_variant), kpi07 — `dash-kpi07-all.point/max_gap`.
Июль — контракт `dash-add-jul.py`/`dash-add-jall.py`: account_summary строки «июль» (31 сут), `rolling_kpi` h = 5 после
`p07-jul-read.patch_july`.

    python tools/compute/dash-add-p08.py --data data/titration-dashboard/data-v40pre.json \\
        --out data/titration-dashboard/data-v40pre2.json

Сверки (assert): базы B1/B2 П-08 == g85b / p07b:h9-cap3 страницы (n и $ по месяцам, B1 ещё kpi07 и счёт целиком);
клетки — n и $ авг/сен и доли kpi07 с `p08-read.json`, n, $ и доля июля с `p08-jul.json`; 8 отобранных = `to_july`.
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


dfn = mod("dfn", "dash-fill-nocalc.py")
k7, ap, merge, RU = dfn.k7, dfn.ap, dfn.merge, dfn.RU
knj, pjr = mod("knj", "kpi-newhigh.py"), mod("pjr", "p07-jul-read.py")
pjr.patch_july(knj)  # отдельный экземпляр kpi-newhigh: у k7 границы авг/сен остаются прежними
JUL = "июль"
SRC = "П-08 §10.3, Судья 2689669"
STATUS = "стоп · П-08 закрыт (Судья 2689669): кандидатов нет"
KPI_NOTE = "П-08: docs/findings/p08-final-2026-09-28.json, Судья 2689669 — все клетки «стоп», кандидатов нет (Холм m = 8)"
TRADES_NOTE = "список сделок на странице только у главных и кандидатов (размер страницы ≤ 14 МБ)"
# гипотеза → (название, строка конфигурации, что фильтрует); признак → краткое имя для кнопки
HYP = {"g36": ("Г-36", "айсберг", "Стена"), "g57": ("Г-57", "допуск по размаху", "Рынок"),
       "g78": ("Г-78", "прострел без BTC", "Рынок"), "g126": ("Г-126", "портфельный тормоз", "Рынок"),
       "g55": ("Г-55", "причина съедания", "Стена"), "g140": ("Г-140", "шок против местного", "Рынок"),
       "g07": ("Г-07", "что за стеной", "Стена")}
FEAT = {"coin_rv_1h": ("RV монеты 1 ч", " bps"), "coin_range_1h": ("размах монеты 1 ч", " bps"),
        "coin_minus_btc_15m": ("монета − BTC за 15 мин", " bps"),
        "n_other_signal_15m": ("других монет с сигналом за 15 мин", ""),
        "g36_ratio": ("исполнено у стены / наибольший видимый", ""),
        "g55_eat_share": ("съедено за 60 с при BTC ≥ −5 bps", ""),
        "g140_pool_share": ("доля монет пула во всплеске 60 с", ""),
        "g07_vs_up": ("глубина позади стены − верхняя треть монеты", ""),
        "g07_vs_mid": ("глубина позади стены − нижняя треть монеты", "")}
OPS = {">": ">", ">=": "≥", "<": "<", "<=": "≤"}


def num(x):
    s = f"{x:.4g}" if isinstance(x, float) and x != int(x) else str(int(x))
    return s.replace(".", ",").replace("-", "−")


def cond(c):
    """Порог клетки из p08-coverage: «RV монеты 1 ч > 46 bps»; у Г-55 `skip>` — пропуск входа."""
    f = c["final"]
    name, unit = FEAT[c["feature"]]
    if f["op"].startswith("skip"):
        return "пропуск, если " + name + " " + OPS[f["op"][4:]] + " " + num(f["thr"])
    return name + " " + OPS[f["op"]] + " " + num(f["thr"]) + unit


def load(d, epochs):
    """Как `dash-p07-all-dump.py`: строки эпох, один потолок, закрытия `[вариант][эпоха][str(max_pos)]`."""
    P = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))
    co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8"))
    v = P["variants"][0]
    grid = [g for g in P["grid"] if g["epoch"] in epochs]
    assert len(grid) == len(epochs), (d, [g["epoch"] for g in P["grid"]])
    mp = {g["max_pos"] for g in grid}
    assert len(mp) == 1, (d, mp)
    mp = mp.pop()
    rec = {"form": v["form"], "set": v["set"], "max_pos": mp, "grid": grid, "closes": {}, "trades": None}
    for g in grid:
        cl = co[v["name"]][g["epoch"]][str(mp)]
        assert g["n"] == len(cl), (d, g["epoch"], g["n"], len(cl))
        rec["closes"][g["epoch"]] = cl
    return rec


def augsep_rec(d):
    r = load(d, list(RU.values()))
    r["closes"] = {pk: r["closes"][RU[pk]] for pk in RU}
    return r


def jul_of(rec, dep):
    acc = merge.account_summary(rec["grid"][0], dep, 31 * 1440)
    roll = knj.rolling_kpi(sorted(tuple(x) for x in rec["closes"][JUL]), h_days=5)["aug"]
    return acc, roll


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--data", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--dir", default="data/p08-dash")
    a.add_argument("--coverage", default="docs/findings/p08-coverage-2026-09-28.json")
    a.add_argument("--final", default="docs/findings/p08-final-2026-09-28.json")
    a = a.parse_args()
    assert os.path.abspath(a.out) != os.path.abspath(a.data), "вход не перезаписывается"
    D = json.load(open(a.data, encoding="utf-8"))
    R = json.load(open(os.path.join(a.dir, "p08-read.json"), encoding="utf-8"))
    J = json.load(open(os.path.join(a.dir, "p08-jul.json"), encoding="utf-8"))["cells"]
    F = json.load(open(a.final, encoding="utf-8"))
    COV = {c["final"]["name"]: c for c in json.load(open(a.coverage, encoding="utf-8"))["cells"]}
    spans = {}
    for p in D["periods"]:
        if p["key"] in ("aug", "sep", "augsep"):
            d0 = dt.datetime.strptime(p["from"], "%Y-%m-%d")
            spans[p["key"]] = int(((dt.datetime.strptime(p["to"], "%Y-%m-%d") - d0).days + 1) * 1440)
    pos, dep = D["position_usd"], D["deposit_usd"]
    A, BV = D["account"], D["nh"]["by_variant"]
    assert F["july"]["cells"] == J, "p08-jul.json ≠ p08-final.july"

    # базы: B1 = g85b (весь счёт и kpi07), B2 = p07b:h9-cap3 (n и $ авг/сен; июля у него на странице нет)
    for bname, run, key in (("B1", "h9-b-p08-keepall", "g85b"), ("B2", "h9-b-p08-keepall-b2", "p07b:h9-cap3")):
        b = augsep_rec(os.path.join(a.dir, "run", run))
        acc, _, bv, _ = dfn.compute(b, pos, dep, spans)
        for pk in RU:
            assert (acc[pk]["n"], acc[pk]["net_usd"]) == (A[pk][key]["n"], A[pk][key]["net_usd"]), (bname, pk)
            assert (acc[pk]["n"], acc[pk]["net_usd"]) == (R["bases"][bname]["n"][pk], R["bases"][bname]["usd"][pk]), (bname, pk)
        frac, n_main = k7.point([tuple(x) for pk in k7.PK for x in b["closes"][pk]])
        assert frac == R["bases"][bname]["frac"] == {pk: BV[key]["kpi07"]["frac"][pk] for pk in k7.PK}, (bname, frac)
        if bname == "B1":
            for pk in ("aug", "sep", "augsep"):
                assert dfn.norm(acc[pk]) == A[pk][key] and dfn.norm(bv[pk]) == BV[key][pk], ("B1 ≠ g85b целиком", pk)
        jb = load(os.path.join(a.dir, "jul", bname), [JUL])
        accj, roll = jul_of(jb, dep)
        assert (accj["n"], accj["net_usd"], roll["frac_gt_h"]) == (J[bname]["n"], J[bname]["usd"], J[bname]["frac_gt_5d"]), bname
        if key in A.get("jul", {}):
            assert dfn.norm(accj) == A["jul"][key] and roll["frac_gt_h"] == BV[key]["kpi07"]["frac"]["jul"], ("B1 июль ≠ g85b", key)
        print(f"база {bname} = {key}: n {acc['aug']['n']}/{acc['sep']['n']}, $ {acc['aug']['net_usd']}/{acc['sep']['net_usd']}, "
              f"июль {accj['n']}/{accj['net_usd']} ({'на странице совпадает' if key in A.get('jul', {}) else 'на странице июля нет'})")

    to_july = set(R["to_july"])
    assert to_july == {c for c, r in J.items() if r.get("selected")}, "to_july ≠ selected в p08-jul"
    metas, lines = [], {}
    A.setdefault("jul", {})
    for cell in sorted(R["cells"], key=lambda c: (list(HYP).index(c.split("-")[1]), c)):
        ref, rj, cov = R["cells"][cell], J[cell], COV[cell]
        hyp = cell.split("-")[1]
        hname, hdesc, row = HYP[hyp]
        key = "p08:" + cell[4:]
        assert key not in A["aug"] and key not in BV, key
        b = augsep_rec(os.path.join(a.dir, "run", "h9-b-" + cell))
        assert b["max_pos"] == ref["cap"], (cell, b["max_pos"], ref["cap"])
        acc, kpi, bv, ncl = dfn.compute(b, pos, dep, spans)
        for pk in RU:
            assert acc[pk]["n"] == ref["n"][pk] and abs(acc[pk]["net_usd"] - ref["usd"][pk]) <= 0.02, (cell, pk, acc[pk]["n"], acc[pk]["net_usd"])
        cl = [tuple(x) for pk in k7.PK for x in b["closes"][pk]]
        frac, n_main = k7.point(cl)
        assert frac == ref["frac"], (cell, frac, ref["frac"])
        gap, gap_c = k7.max_gap(cl)
        jr = load(os.path.join(a.dir, "jul", cell), [JUL])
        assert jr["max_pos"] == ref["cap"] and jr["form"] == b["form"] and jr["set"] == b["set"], cell
        accj, roll = jul_of(jr, dep)
        # $ — допуск 0,02, как сверка stage12/TK-009 в dash-add-p07-all (округление total_usd −10,715 → −10,71 / −10,72)
        assert accj["n"] == rj["n"] and abs(accj["net_usd"] - rj["usd"]) <= 0.02, (cell, accj["n"], accj["net_usd"], rj["n"], rj["usd"])
        assert (roll["frac_gt_h"], roll["n_main"]) == (rj["frac_gt_5d"], rj["n_main"]), (cell, roll["frac_gt_h"])
        for pk in ("aug", "sep", "augsep"):
            A[pk][key] = acc[pk]
        assert not kpi, cell  # сделок в выгрузке нет — kpi по сделкам не считается (как у trades = None)
        A["jul"][key] = accj
        BV[key] = bv
        sel = cell in to_july
        BV[key]["kpi07"] = {
            "frac": {**frac, "jul": roll["frac_gt_h"]}, "n_main": {**n_main, "jul": roll["n_main"]},
            "verdict": ref["state"], "note": "вердикт авг/сен — p08-read (П-08 §10.2, Судья 2689669); доля пересчитана из закрытий",
            "max_gap_h": gap, "max_gap_censored": gap_c,
            "max_gap_h_jul": None if roll["max"]["max"] is None else round(roll["max"]["max"], 1),
            "max_gap_censored_jul": roll["max"]["max_censored"], "states": {"jul": rj["state"]},
            "verdict_jul": ("проверка на июле: " + rj["verdict"]) if sel else "описание, не отобран",
            "verdict_jul_src": SRC if sel else "TK-018: июль для всех вариантов (В-151)"}
        assert not sel or rj["verdict"] == "стоп", cell
        base_txt = "Г-85б с потолком 3 позиции (p07b:h9-cap3, база B2)" if ref["cap"] else "Г-85б (g85b, база B1)"
        c = cond(cov)
        metas.append({
            "key": key, "group": "p08", "pill": f"{hname} (П-08) · {c}", "btn": c, "axis": f"{hname} {hdesc}",
            "label": f"{hname} {hdesc} (П-08, {cov['role']} клетка {cell}): вход только если {c}"
                     f" (признак {cov['feature']}, порог — p08-coverage); остальное как в базе {base_txt}."
                     f" Счёт — portfolio-sim прогона П-08 (busy-replay, без TRX), форма {b['form']}, набор {b['set']}",
            "status": STATUS, "kpi_note": KPI_NOTE, "n_closes": ncl, "trades_note": TRADES_NOTE,
            "base": "g85b", "cfg": {row: f"{hname}: {c}", **({"Ограничения": "не больше 3 позиций"} if ref["cap"] else {})}})
        lines.setdefault(f"{hname} {hdesc}", []).append(key)
        print(f"{key:15s} n {acc['aug']['n']}/{acc['sep']['n']}/{accj['n']}  $ {acc['aug']['net_usd']}/{acc['sep']['net_usd']}/{accj['net_usd']}"
              f"  kpi07 {frac['aug']}/{frac['sep']}/{roll['frac_gt_h']}  {BV[key]['kpi07']['verdict_jul']}")
    assert len(metas) == 12, len(metas)
    D["variants"] += metas
    D["vrows"].append({"title": "П-08 · новый код, 7 гипотез (стоп, Судья 2689669)", "note": KPI_NOTE,
                       "lines": [{"axis": ax, "keys": ks} for ax, ks in lines.items()]})
    D["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
    print(f"добавлено {len(metas)} клеток П-08; {a.out}: {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
