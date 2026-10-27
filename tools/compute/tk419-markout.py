#!/usr/bin/env python3
"""TK-419 марк-аут вперёд событий пампа (протокол docs/research/P-TK419-pump-markout-2026-10-27.md, утверждён Судьёй).
M_h = s·(close[τ+h]−close[τ])/close[τ]·1e4, бп, h∈{5,15,60,240} мин; свечи 1 м — те же, что в детекторе TK-414 (load+despike TK-413).
Выход — строка на событие θs=5: признаки (из obfeat-events), класс T/R/P/O, форма ноги, M_h (пусто — τ+h за концом данных).
Использование: tk419-markout.py <obfeat-events.csv> <pref-legs.csv> <legs-слоя2.csv> <корень свечей> <рынки через ,> <выход.csv> [процессов=15]"""
import bisect
import csv
import importlib.util
import os
import sys
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tk413", os.path.join(HERE, "tk413-episodes.py"))
T13 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T13)
HS = (5, 15, 60, 240)


def tm(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() / 60


def work(arg):
    root, k, rows = arg
    try:
        t, c, q, n, w = T13.load(root, k[0], k[1], k[2])
    except FileNotFoundError:
        return [], len(rows)
    t, c, q, n, w, _ = T13.despike(t, c, q, n, w)
    out = []
    tmin = [x // 60000 for x in t]
    for r in rows:
        i = bisect.bisect_left(tmin, int(r["i"]))
        if i >= len(tmin) or tmin[i] != int(r["i"]):
            i = bisect.bisect_right(tmin, int(r["i"])) - 1
            if i < 0:
                continue
        p0 = c[i]
        o = dict(r)
        for h in HS:
            tgt = tmin[i] + h
            if tgt > tmin[-1]:
                o["M%d" % h] = ""
                continue
            j = bisect.bisect_right(tmin, tgt) - 1  # пропуск минуты — последняя известная цена
            o["M%d" % h] = "%.3f" % (r["s"] * (c[j] - p0) / p0 * 1e4)
        out.append(o)
    return out, 0


def main():
    ev_f, leg_f, l2_f, root, mk, outp = sys.argv[1:7]
    nproc = int(sys.argv[7]) if len(sys.argv) > 7 else 15
    MARKETS = mk.split(",")
    forms = {}
    for r in csv.DictReader(open(l2_f, newline="", encoding="utf-8")):
        forms[(r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"])] = r["форма"]
    legs = {}
    for r in csv.DictReader(open(leg_f, newline="", encoding="utf-8")):
        k = (r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["t0"])
        legs[k] = dict(t0=tm(r["t0"]), bad=r["плохая"] == "1",
                       form=forms.get((r["биржа"], r["рынок"], r["инструмент"], "памп" if r["знак"] == "1" else "дамп", r["t0"]), ""))
    by = {}
    nofeat = tot = 0
    cols = None
    for r in csv.DictReader(open(ev_f, newline="", encoding="utf-8")):
        cols = cols or list(r.keys())
        if r["рынок"] not in MARKETS or r["вариант"] != "5":
            continue
        r.setdefault("oi_surge", ""); r.setdefault("oi_dir", "")
        s = int(r["знак"])
        if r["нога_плохая"] == "1" or r["обратная_плохая"] == "1":
            continue
        k = (r["биржа"], r["рынок"], r["инструмент"])
        ext = tm(r["t_оценка_старта"])
        lk = k + (str(s), r["нога_того_же_знака"]) if r["нога_того_же_знака"] else None
        ok = k + (str(-s), r["нога_обратная"]) if r["нога_обратная"] else None
        if lk:
            r["cls"] = "T" if abs(ext - legs[lk]["t0"]) < 1 else "R"
            r["form"] = legs[lk]["form"]
        elif ok:
            r["cls"] = "P"
            r["form"] = legs[ok]["form"]
        else:
            r["cls"] = "O"
            r["form"] = ""
        r["i"], r["s"] = int(tm(r["t_событие"])), s
        tot += 1
        if r["surge"] == "" or r["ob_dz"] == "":  # протокол: без признака — исключено из всех правил популяции
            nofeat += 1
            continue
        by.setdefault(k, []).append(r)
    print("событий", tot, "без признака", nofeat, "инструментов", len(by), flush=True)
    out, lost = [], 0
    with Pool(nproc) as pool:
        for o, l in pool.imap_unordered(work, [(root, k, v) for k, v in by.items()], chunksize=1):
            out += o
            lost += l
    names = ["биржа", "рынок", "инструмент", "t_событие", "знак", "cls", "form", "imbB", "surgeB", "oi_surge", "oi_dir", "ob_dz", "ob_imb", "ob_sp"] + ["M%d" % h for h in HS]
    with open(outp, "w", newline="") as f:
        wr = csv.DictWriter(f, names, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(sorted(out, key=lambda x: (x["инструмент"], x["t_событие"])))
    print("записано", len(out), "без файла свечей (инстр.)", lost, flush=True)


if __name__ == "__main__":
    main()
