#!/usr/bin/env python3
"""KPI П-07 «доля часов ожидания нового максимума > 5 сут» (kpi07.frac, авг/сен) для ВСЕХ вариантов дашборда (TK-008).

Тот же счёт, что у 9 уже посчитанных (`kpi-newhigh.py`: `rolling_kpi(закрытия авг+сен, h_days=5)` → `frac_gt_h`,
`n_main`), только без устойчивости и бутстрепа (дорого, не для 260 рядов). Закрытия: главный/П-02/П-05 — `data/kpi/
*-closes.json` (`kpi-newhigh.load`, ключ страницы — `kpi-dash-data.page_key`); П-07 и Г-85б — `p07-all-r2.json`
(выгрузка portfolio-sim с деки, ключ — `dash-add-p07-all.suffix`). Сетку не гонять. Вердикт — только перенесённый
из проверенного Судьёй файла (ступени 1–2 Г-85а `p07-stage12`, ступень 2 Г-85б `p07-tk004-stage2`), иначе «только
доля». Готовые kpi07 (9 рядов) не трогаются, но пересчитываются для сверки. Остальное = входной файл (v35 → v36: + max_gap_h, вердикт H6 Г-85а).

    python tools/compute/dash-kpi07-all.py --in data/titration-dashboard/data-v35.json \\
        --out data/titration-dashboard/data-v36.json
"""
import argparse
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def mod(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


kn = mod("kn", "kpi-newhigh.py")
kd = mod("kd", "kpi-dash-data.py")
ap = mod("ap", "dash-add-p07-all.py")
PK = ("aug", "sep")


def stage12_key(name):
    """Имя клетки p07-stage12 → ключ страницы: base → g85, H6-pct1.5 → p07a:h6-pct15, H10-cap3 → p07a:h9-cap3."""
    if name == "base":
        return "g85"
    return "p07a:" + _cell(name.split("(")[0].replace("before", "behind"))


def _cell(name):
    """Общая часть имён клеток обоих файлов: H6-pct1.5 → h6-pct15, H10-cap3 → h9-cap3 (потолок = h9-cap в выгрузке),
    H9-pauseStop60 → h9-pauseStop60 (регистр значения сохраняется), H14-k1/H9r-s1 → h9r-k1/h9r-s1, keep-keep → h9r-keep."""
    if name == "keep-keep":
        return "h9r-keep"
    ax, val = name.split("-", 1)
    ax = {"H10": "h9", "H14": "h9r", "H9r": "h9r"}.get(ax, ax.lower())
    return ax + "-" + val.replace(".", "")


def tk004_key(name):
    """Имя клетки p07-tk004-stage2 (g85b) → ключ: base → g85b, H2-0.0136 → p07b:h2-00136."""
    if name == "base":
        return "g85b"
    return "p07b:" + _cell(name)


def point(closes):
    r = kn.rolling_kpi(sorted(closes), h_days=5)
    return {pk: r[pk]["frac_gt_h"] for pk in PK}, {pk: r[pk]["n_main"] for pk in PK}


def max_gap(closes):
    """Добавка владельца 28.09 ~01:40 (TK-008, CEO): «max дней до перехая» — наибольшее ожидание нового максимума
    счёта «от максимума» по часовой сетке t на счёте авг+сен одним счётом (= `rolling_kpi` roll.max.max, как
    «до перехая, максимум» у главного, 403,9 ч), максимум по обоим месяцам; `censored` — если он сам упёрся в
    конец данных 24.09 (истинный больше)."""
    r = kn.rolling_kpi(sorted(closes), h_days=5)
    m = max((r[pk]["max"] for pk in PK if r[pk]["max"]["max"] is not None), key=lambda x: x["max"])
    return round(m["max"], 1), m["max_censored"]


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--in", dest="inp", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--closes-dir", default="data/kpi")
    a.add_argument("--p07", default="data/titration-dashboard/p07-all-r2.json")
    a.add_argument("--stage12", default="docs/findings/p07-stage12-2026-09-27.json")
    a.add_argument("--tk004", default="docs/findings/p07-tk004-stage2-2026-09-28.json")
    a = a.parse_args()
    D = json.load(open(a.inp, encoding="utf-8"))
    BV = D["nh"]["by_variant"]

    closes = {}
    for name, (_g, ser) in kn.load(a.closes_dir).items():
        key = kd.page_key(name)
        if key:
            closes.setdefault(key, ser["augsep"])
    for dirname, b in json.load(open(a.p07, encoding="utf-8")).items():
        side, suf = ap.suffix(dirname)
        key = {"read-a-base": "g85", "read-b-base": "g85b"}.get(dirname, f"p07{side}:{suf}")
        if b.get("closes"):
            cl = [tuple(x) for pk in PK for x in b["closes"][pk]]
            if key in closes:  # g85 из обоих источников — должен совпасть
                assert point(closes[key])[0] == point(cl)[0], key
            closes.setdefault(key, cl)

    reviewed = {}
    s12 = json.load(open(a.stage12, encoding="utf-8"))["cells"]
    for name, c in s12.items():
        if not c.get("kpi"):  # клетка без KPI в ступени (мало сделок) — вердикта нет
            continue
        reviewed[stage12_key(name)] = ("p07-stage12-2026-09-27", c["kpi"]["frac_gt_5d"], c["kpi"]["verdict"])
    for name, c in json.load(open(a.tk004, encoding="utf-8"))["g85b"].items():
        reviewed[tk004_key(name)] = ("p07-tk004-stage2-2026-09-28", c["frac"], c["verdict"])
    for name, c in json.load(open(a.tk004, encoding="utf-8"))["g85a_h6"].items():  # Судья 1bd98e8 п.1: H6 at/behind Г-85а
        if name != "base":
            reviewed[stage12_key(name)] = ("p07-tk004-stage2-2026-09-28 (g85a_h6)", c["frac"], c["verdict"])

    bad, added, same, nocl = [], 0, 0, []
    for key, v in BV.items():
        if key not in closes:
            nocl.append(key)
            continue
        frac, n_main = point(closes[key])
        gap, gap_c = max_gap(closes[key])
        if "kpi07" in v:
            same += 1
            if v["kpi07"]["frac"] != frac or v["kpi07"]["n_main"] != n_main:
                bad.append(("готовый kpi07", key, v["kpi07"]["frac"], frac))
            v["kpi07"].update(max_gap_h=gap, max_gap_censored=gap_c)
            if v["kpi07"].get("verdict") == "только доля" and key in reviewed:
                src, rfrac, verdict = reviewed[key]
                if rfrac != frac:
                    bad.append((src, key, rfrac, frac))
                v["kpi07"].update(verdict=verdict, note=f"вердикт — из {src} (проверено Судьёй); доля пересчитана из закрытий")
            continue
        k07 = {"frac": frac, "n_main": n_main, "max_gap_h": gap, "max_gap_censored": gap_c, "verdict": "только доля",
               "note": "точка на двух месяцах; устойчивость по суткам/монете и бутстреп не считались (TK-008)"}
        if key in reviewed:
            src, rfrac, verdict = reviewed[key]
            if rfrac != frac:
                bad.append((src, key, rfrac, frac))
            k07.update(verdict=verdict, note=f"вердикт — из {src} (проверено Судьёй); доля пересчитана из закрытий")
        v["kpi07"] = k07
        added += 1
    miss = sorted(k for k in reviewed if k not in BV)
    print(f"вариантов {len(BV)}: kpi07 добавлен {added}, готовых сверено {same}, без закрытий {len(nocl)} {nocl}")
    print(f"вердикт перенесён из проверенных: {sum(1 for k in reviewed if k in BV)}; имён проверенных без ключа: {miss}")
    print("g85b:", BV["g85b"]["kpi07"]["frac"], "g85:", BV["g85"]["kpi07"]["frac"])
    for key, ref in (("btc4h_trail", 403.9), ("g85", 383.3), ("g85b", 320.3), ("cand", 617.9)):  # сверка CEO 01:39
        got = BV[key]["kpi07"]["max_gap_h"]
        print(f"max_gap_h {key}: {got} (сверка {ref})")
        if got != ref:
            bad.append(("max_gap_h", key, ref, got))
    for b in bad:
        print("РАСХОЖДЕНИЕ", *b)
    if bad or miss:
        raise SystemExit(1)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
