#!/usr/bin/env python3
"""TK-420 вар. 4 сводка (протокол P-TK420-pump-dynamics-end-month2-2026-10-27.md §«Вар. 4»): Ek (E1,E2,E3) против минут E0 (E3 — E0' = не E0) того же окна, не выбранных правилом Ek.
Кластер — инструменто-сутки старта окна; прореживание внутри группы по инструменту (шаг h мин); бутстрап 10^4; Бонферрони по делителю семьи вар. 4 (A 12); «различает» — разность > 0 (M уже в сторону разворота),
нижняя граница Бонферрони > 0, знак тот же в обеих половинах месяца; < 30 инструменто-суток в выбранных — «мало данных». Справочно: Ek − все остальные кандидат-минуты; E0 − не-E0; доля остановленных окон и MLast.
Использование: tk420-var4-sum.py <var4-A.csv> <выход.csv> [B=10000] > сводка.md"""
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

src, outcsv = sys.argv[1:3]
NB = int(sys.argv[3]) if len(sys.argv) > 3 else 10000
HS = (5, 15, 60, 240)
DIV = 12
rng = np.random.default_rng(420)
rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
for r in rows:
    r["min"] = int(r["m"])
    r["day"] = (r["инструмент"], r["день"])
    r["half"] = 1 if (date(1970, 1, 1) + timedelta(days=int(r["день"]))).day <= 15 else 2
    r["e"] = {k: r[k] == "1" for k in ("E0", "E1", "E2", "E3")}
    r["e"]["E0p"] = not r["e"]["E0"]


def thin(ev, h):
    """внутри группы, по инструменту: первое событие, следующее не раньше чем через h мин."""
    out, last = [], {}
    for r in sorted(ev, key=lambda r: (r["инструмент"], r["min"])):
        k = r["инструмент"]
        if k in last and r["min"] < last[k] + h:
            continue
        last[k] = r["min"]
        out.append(r)
    return out


def per_inst(ev, h):
    d = defaultdict(list)
    for r in ev:
        if r["M%d" % h] != "":
            d[r["инструмент"]].append(float(r["M%d" % h]))
    return d


def med_inst(d, minn=5):
    return {k: float(np.median(v)) for k, v in d.items() if len(v) >= minn}


def boot(ga, gb, h, B):
    """медиана по инструментам (a) − (b), кластерный бутстрап по инструменто-суткам объединения групп; только инструменты ≥5 в обеих группах."""
    keys = sorted({r["day"] for r in ga + gb})
    kid = {k: i for i, k in enumerate(keys)}
    cnt = rng.multinomial(len(keys), [1 / len(keys)] * len(keys), size=B).astype(np.int32)
    da, db = defaultdict(list), defaultdict(list)
    for g, d in ((ga, da), (gb, db)):
        for r in g:
            if r["M%d" % h] != "":
                d[r["инструмент"]].append((float(r["M%d" % h]), kid[r["day"]]))
    insts = [k for k in da if k in db and len(da[k]) >= 5 and len(db[k]) >= 5]
    if not insts:
        return None
    def wmed(lst):
        lst = sorted(lst)
        v = np.array([x[0] for x in lst]); idx = np.array([x[1] for x in lst])
        w = cnt[:, idx]
        cs = np.cumsum(w, axis=1)
        tot = cs[:, -1]
        pos = (cs * 2 >= tot[:, None]).argmax(axis=1)
        m = v[pos].astype(float)
        m[tot == 0] = np.nan
        return m
    ma = np.stack([wmed(da[k]) for k in insts], 1)
    mb = np.stack([wmed(db[k]) for k in insts], 1)
    d = np.nanmedian(ma, 1) - np.nanmedian(mb, 1)
    return insts, d


def q3(x):
    return tuple(np.percentile(x, [25, 50, 75])) if x else (np.nan,) * 3


def cell(name, ga, gb, h, label_a, label_b):
    ta, tb = thin(ga, h), thin(gb, h)
    ia, ib = med_inst(per_inst(ga, h)), med_inst(per_inst(gb, h))
    both = [k for k in ia if k in ib]
    pa = med_inst(per_inst(ta, h), 1); pb = med_inst(per_inst(tb, h), 1)
    res = dict(правило=name, h=h, группа_a=label_a, группа_b=label_b, n_a=len(ga), n_b=len(gb), n_a_пр=len(ta), n_b_пр=len(tb),
               инстр_a=len(ia), инстр_b=len(ib), инстр_обе=len(both))
    qa, qb = q3(list(ia.values())), q3(list(ib.values()))
    res.update(a_q25=qa[0], a_med=qa[1], a_q75=qa[2], b_q25=qb[0], b_med=qb[1], b_q75=qb[2])
    if len(both) < 5:
        res.update(diff="", lo="", hi="", lo_b="", hi_b="", полуразн="", вердикт_ячейки="мало данных")
        return res
    res["diff"] = float(np.median([ia[k] for k in both]) - np.median([ib[k] for k in both]))
    # интервал — по прореженной выборке (правило вердикта)
    bt = boot(ta, tb, h, NB)
    if bt is None:
        res.update(lo="", hi="", lo_b="", hi_b="", полуразн="", вердикт_ячейки="мало данных (прореж.)")
        return res
    _, d = bt
    d = d[~np.isnan(d)]
    res["lo"], res["hi"] = np.percentile(d, [2.5, 97.5])
    a = 5.0 / DIV
    res["lo_b"], res["hi_b"] = np.percentile(d, [a / 2, 100 - a / 2])
    hv = []
    for hf in (1, 2):
        xa = [r for r in ga if r["half"] == hf]; xb = [r for r in gb if r["half"] == hf]
        ja, jb = med_inst(per_inst(xa, h)), med_inst(per_inst(xb, h))
        bo = [k for k in ja if k in jb]
        hv.append(float(np.median([ja[k] for k in bo]) - np.median([jb[k] for k in bo])) if len(bo) >= 3 else float("nan"))
    res["полуразн"] = "%.1f/%.1f" % tuple(hv)
    sign_ok = all(not np.isnan(x) and x > 0 for x in hv)
    res["вердикт_ячейки"] = "различает" if res["diff"] > 0 and res["lo_b"] > 0 and sign_ok else "нет"
    return res



def mcell(name, ga, gb, h, la, lb):
    res = cell(name, ga, gb, h, la, lb)
    ncl = len({r["day"] for r in ga if r["M%d" % h] != ""})
    res["кластеров_a"] = ncl
    if ncl < 30:
        res["вердикт_ячейки"] = "мало данных (<30 инструменто-суток)"
    return res


out = []
cand = rows
for rule, base in (("E1", "E0"), ("E2", "E0"), ("E3", "E0p")):
    for h in HS:
        sel = [r for r in cand if r["e"][rule]]
        ctl = [r for r in cand if r["e"][base] and not r["e"][rule]]
        rest = [r for r in cand if not r["e"][rule]]
        out.append(mcell(rule + " vs " + base, sel, ctl, h, rule, base + "-не-" + rule))
        out.append(mcell(rule + " vs все (справочно)", sel, rest, h, rule, "прочие"))
for h in HS:
    out.append(mcell("E0 vs не-E0 (контроль)", [r for r in cand if r["e"]["E0"]], [r for r in cand if not r["e"]["E0"]], h, "E0", "не-E0"))
cols = list(out[0].keys())
with open(outcsv, "w", newline="", encoding="utf-8") as fo:
    wr = csv.DictWriter(fo, cols)
    wr.writeheader()
    wr.writerows(out)
wins = {(r["инструмент"], r["окно"]): r for r in rows}
stopped = sum(1 for r in wins.values() if r["окно_остановлено"] == "1")
print("# TK-420 вар. 4, популяция A (Bybit linear, сентябрь)\n")
print("Кандидат-минут %d, окон %d (остановлено разворотом θs=5: %d, %.1f %%), инструментов %d. Бутстрап %d, Бонферрони делитель %d (E3 — описательно). M — бп разворота (−s·Δ) от закрытия минуты, медиана по инструментам (≥5).\n" % (
    len(rows), len(wins), stopped, 100 * stopped / max(1, len(wins)), len({r["инструмент"] for r in rows}), NB, DIV))
print("Минуты: E0 %d, E1 %d, E2 %d, E3 %d.\n" % tuple(sum(1 for r in rows if r["e"][k]) for k in ("E0", "E1", "E2", "E3")))
print("Контроль артефакта определения: M_h от закрытия ПОСЛЕДНЕЙ минуты окна (по окнам; медиана по инструментам, ≥5 окон):\n")
print("| h | окон | инстр. | мед. по инстр. | q25 | q75 |")
print("|---|---|---|---|---|---|")
for h in HS:
    d = defaultdict(list)
    for r in wins.values():
        if r["MLast%d" % h] != "":
            d[r["инструмент"]].append(float(r["MLast%d" % h]))
    mi = med_inst(d)
    q = q3(list(mi.values()))
    print("| %d | %d | %d | %.1f | %.1f | %.1f |" % (h, sum(len(v) for v in d.values()), len(mi), q[1], q[0], q[2]))
print()
print("| правило | h | сравнение | n a/b (прорежено) | инстр. a/b/обе | кластеров a | мед. a [q25;q75] | мед. b [q25;q75] | разность | 95% | 95% Бонф. | половины 1/2 | ячейка |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
def fm(x):
    return "" if x == "" or (isinstance(x, float) and np.isnan(x)) else "%.1f" % x
for r in out:
    print("| %s | %d | %s − %s | %d/%d (%d/%d) | %d/%d/%d | %d | %s [%s;%s] | %s [%s;%s] | %s | %s…%s | %s…%s | %s | %s |" % (
        r["правило"], r["h"], r["группа_a"], r["группа_b"], r["n_a"], r["n_b"], r["n_a_пр"], r["n_b_пр"], r["инстр_a"], r["инстр_b"], r["инстр_обе"], r["кластеров_a"],
        fm(r["a_med"]), fm(r["a_q25"]), fm(r["a_q75"]), fm(r["b_med"]), fm(r["b_q25"]), fm(r["b_q75"]), fm(r["diff"]), fm(r.get("lo", "")), fm(r.get("hi", "")),
        fm(r.get("lo_b", "")), fm(r.get("hi_b", "")), r.get("полуразн", ""), r["вердикт_ячейки"]))
