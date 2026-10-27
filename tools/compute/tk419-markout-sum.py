#!/usr/bin/env python3
"""TK-419 сводка марк-аута (протокол P-TK419-pump-markout-2026-10-27.md): принятые vs непринятые vs O, прореживание, кластерный бутстрап по инструменто-суткам.
Использование: tk419-markout-sum.py <markout.csv> <A|B> <выход.csv> [B=10000] > сводка.md"""
import csv
import sys
from collections import defaultdict

import numpy as np

src, pop, outcsv = sys.argv[1:4]
NB = int(sys.argv[4]) if len(sys.argv) > 4 else 10000
HS = (5, 15, 60, 240)
DIV = {"A": 20, "B": 16}[pop]
rng = np.random.default_rng(419)


def f(x):
    return float(x) if x != "" else None


rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
for r in rows:
    r["t"] = r["t_событие"]
    r["min"] = int(np.datetime64(r["t_событие"]).astype("datetime64[m]").astype(np.int64))
    r["day"] = r["t_событие"][:10]
    r["half"] = 1 if int(r["t_событие"][8:10]) <= 15 else 2
    sb, ib, dz, sp, oi = f(r["surgeB"]), f(r["imbB"]), f(r["ob_dz"]), f(r["ob_sp"]), f(r["oi_surge"])
    B = sb is not None and ib is not None and sb >= 3 and ib >= 0.1
    O1 = dz is not None and dz <= 1 / 3
    O3 = sp is not None and sp >= 3
    r["acc"] = {"B": B, "O1": O1, "O3": O3, "O4": B and O1, "R5": B and oi is not None and oi >= 3 and r["oi_dir"] == "1"}
RULES = ["B", "O1", "O3", "O4"] + (["R5"] if pop == "A" else [])


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
    keys = sorted({(r["инструмент"], r["day"]) for r in ga + gb})
    kid = {k: i for i, k in enumerate(keys)}
    cnt = rng.multinomial(len(keys), [1 / len(keys)] * len(keys), size=B).astype(np.int32)
    da, db = defaultdict(list), defaultdict(list)
    for g, d in ((ga, da), (gb, db)):
        for r in g:
            if r["M%d" % h] != "":
                d[r["инструмент"]].append((float(r["M%d" % h]), kid[(r["инструмент"], r["day"])]))
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


out = []
nbad = 0
for rule in RULES:
    for h in HS:
        pool = [r for r in rows if r["M%d" % h] != "" or True]
        acc = [r for r in pool if r["acc"][rule]]
        non = [r for r in pool if not r["acc"][rule]]
        out.append(cell(rule, acc, non, h, "принятые", "непринятые"))
        out.append(cell(rule, acc, [r for r in pool if r["cls"] == "O"], h, "принятые", "класс O"))
        out.append(cell(rule, [r for r in acc if r["cls"] == "O"], [r for r in acc if r["cls"] != "O"], h, "принятые∩O", "принятые∩(T,R,P)"))
cols = list(out[0].keys())
with open(outcsv, "w", newline="", encoding="utf-8") as fo:
    wr = csv.DictWriter(fo, cols)
    wr.writeheader()
    wr.writerows(out)
n_total = len(rows)
print("# TK-419 марк-аут, популяция %s (%s)\n" % (pop, "Bybit linear" if pop == "A" else "Gate futures_usdt"))
print("Событий (θs=5, с признаками): %d, инструментов %d. Бутстрап %d, Бонферрони делитель %d. M — бп вперёд по знаку события, медиана по инструментам (≥5 событий).\n" % (n_total, len({r['инструмент'] for r in rows}), NB, DIV))
print("| правило | h | сравнение | n a/b (прорежено) | инстр. a/b/обе | мед. a [q25;q75] | мед. b [q25;q75] | разность | 95% | 95% Бонф. | половины 1/2 | ячейка |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
def fm(x):
    return "" if x == "" or (isinstance(x, float) and np.isnan(x)) else "%.1f" % x
for r in out:
    print("| %s | %d | %s − %s | %d/%d (%d/%d) | %d/%d/%d | %s [%s;%s] | %s [%s;%s] | %s | %s…%s | %s…%s | %s | %s |" % (
        r["правило"], r["h"], r["группа_a"], r["группа_b"], r["n_a"], r["n_b"], r["n_a_пр"], r["n_b_пр"], r["инстр_a"], r["инстр_b"], r["инстр_обе"],
        fm(r["a_med"]), fm(r["a_q25"]), fm(r["a_q75"]), fm(r["b_med"]), fm(r["b_q25"]), fm(r["b_q75"]), fm(r["diff"]), fm(r.get("lo", "")), fm(r.get("hi", "")),
        fm(r.get("lo_b", "")), fm(r.get("hi_b", "")), r.get("полуразн", ""), r["вердикт_ячейки"]))
