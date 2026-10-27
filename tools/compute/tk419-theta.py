#!/usr/bin/env python3
"""TK-419 вар. 6: классы T/R/P/O событий θs=5 при эталоне θ∈{6,7 %} (зигзаг TK-413 по закрытиям 1 м, despike как в детекторе).
Класс как в tk414-flow2.py: нога того же знака с a<=i<=b → T (оценка старта = старт ноги, <1 мин) / R; иначе нога обратного знака → P; иначе O.
Ноги с плохим принтом при новых θ не размечены (оговорка протокола): исключены лишь события, помеченные плохими ногами θ=10,48 в obfeat-events.
Использование: tk419-theta.py <obfeat-events.csv> <корень свечей> <рынки> <выход.csv> [процессов]"""
import bisect, csv, importlib.util, os, sys
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tk413", os.path.join(HERE, "tk413-episodes.py"))
T13 = importlib.util.module_from_spec(spec); spec.loader.exec_module(T13)
THETAS = (0.06, 0.07)


def tmin(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() // 60)


def work(arg):
    root, k, rows = arg
    t, c, q, n, w = T13.load(root, k[0], k[1], k[2])
    t, c, q, n, w, _ = T13.despike(t, c, q, n, w)
    tm = [x // 60000 for x in t]
    legs = {th: T13.zigzag(c, th) for th in THETAS}
    out = []
    for r in rows:
        i = bisect.bisect_left(tm, tmin(r["t_событие"]))
        e = bisect.bisect_left(tm, tmin(r["t_оценка_старта"]))
        s = int(r["знак"])
        o = dict(r)
        for th in THETAS:
            ls = [(a, b) for (a, b, sg, d) in legs[th] if sg == s and a <= i <= b]
            lo = [(a, b) for (a, b, sg, d) in legs[th] if sg == -s and a <= i <= b]
            if ls:
                o["cls%d" % round(th * 100)] = "T" if abs(tm[e] - tm[ls[0][0]]) < 1 else "R"
            elif lo:
                o["cls%d" % round(th * 100)] = "P"
            else:
                o["cls%d" % round(th * 100)] = "O"
        out.append(o)
    return out


def main():
    ev_f, root, mk, outp = sys.argv[1:5]
    nproc = int(sys.argv[5]) if len(sys.argv) > 5 else 15
    by = {}
    for r in csv.DictReader(open(ev_f, newline="", encoding="utf-8")):
        if r["рынок"] not in mk.split(",") or r["вариант"] != "5" or r["нога_плохая"] == "1" or r["обратная_плохая"] == "1":
            continue
        if r["surge"] == "" or r["ob_dz"] == "":
            continue
        by.setdefault((r["биржа"], r["рынок"], r["инструмент"]), []).append(r)
    out = []
    with Pool(nproc) as p:
        for o in p.imap_unordered(work, [(root, k, v) for k, v in by.items()], chunksize=1):
            out += o
    names = ["биржа", "рынок", "инструмент", "t_событие", "знак", "imbB", "surgeB", "ob_dz", "ob_sp", "cls6", "cls7"]
    with open(outp, "w", newline="") as f:
        wr = csv.DictWriter(f, names, extrasaction="ignore"); wr.writeheader(); wr.writerows(out)
    print("записано", len(out), flush=True)


main()
