#!/usr/bin/env python3
"""TK-433 шаг 1 (протокол P-TK433 v9b + уточнения Судьи): ноги зигзага по цене, E1/E3/E4, склейка, воронка. Признаков нет.
Использование: tk433-legs.py <корень свечей> <b1-thresh.json> <l4v8-ГГГГ-ММ.json> <art.json> <выход-каталог> [процессов] [месяц] [T_θ=60]
Нога по close 1 м инструмента: состояние «вверх» — бегущий максимум; закрытие ≤ max·exp(−θ) подтверждает ногу (старт — минимум
до неё, пик — максимум), дальше «вниз» симметрично. θ_знака = a*_Tθ рынка (b1, срез «м», без новых чисел; нога вверх — θ «+», вниз — θ «−»).
Неподтверждённая нога на конце месяца не берётся. Длительность = t_peak − t_start ≥ 90 мин (число владельца).
E1 имя (3L/3S/5L/5S, BULL/BEAR/UP/DOWN); E3 нога [t_start, t_peak] пересекает рыночный период L4 v8 своего знака своей биржи-рынка
(любой T из l4v8.json); E4 фитиль: ход ноги за вычетом лучшего 10-мин блока (по close, в сторону ноги) < θ.
E2 (артефакты бирж) — минуты `art.json` не читаются (mv.EXCL), как в TK-421. Порядок ступеней воронки: ≥90 мин → E1 → E3 → E4.
Склейка: (а) пара — соседние ноги вверх→вниз (дамп стартует в пике своей ноги), обе прошли ≥90 мин; (б) кластер по монете
(b2b.coin) на рынках/биржах: пересечение окон [t_start, t_end] — один кластер, представитель — рынок с наибольшим оборотом ноги."""
import csv
import importlib.util
import json
import math
import os
import re
import sys
from collections import defaultdict, deque
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("ev", os.path.join(HERE, "tk421-events.py"))
ev = importlib.util.module_from_spec(sp)
sp.loader.exec_module(ev)
mv, MIN = ev.mv, ev.MIN
MIN_LEG = 90          # мин, число владельца
BLOCK = 10            # мин, E4 (верх фитилей 3–10 мин по просмотру PNG TK-421)
LEV = re.compile(r"(\d+[LS]|BULL|BEAR|UP|DOWN)(_?USDT?|_?USD)?$")
TH, PER, TTH = {}, {}, 60


def coin(sym):
    s = sym.split("-")[0]
    for suf in ("_USDT", "USDT", "_USD", "USD"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def zigzag(t, c, th_up, th_dn):
    """подтверждённые ноги: [(знак, i_старт, i_пик)]; i_пик — экстремум конца ноги."""
    legs = []
    if len(c) < 3:
        return legs
    up, i_lo, i_hi, i_start = True, 0, 0, 0
    d_up, d_dn = math.exp(-th_up), math.exp(th_dn)
    for i in range(1, len(c)):
        if up:
            if c[i] > c[i_hi]:
                i_hi = i
            elif c[i] <= c[i_hi] * d_up:
                legs.append(("+", i_start, i_hi))
                up, i_start, i_lo = False, i_hi, i
        else:
            if c[i] < c[i_lo]:
                i_lo = i
            elif c[i] >= c[i_lo] * d_dn:
                legs.append(("-", i_start, i_lo))
                up, i_start, i_hi = True, i_lo, i
    return legs


def best_block(t, c, i0, i1, sgn):
    """лучший ход (ln, в сторону ноги) внутри ноги за ≤ BLOCK минут по close."""
    dq, best = deque(), 0.0
    for j in range(i0, i1 + 1):
        while dq and t[dq[0]] < t[j] - BLOCK * MIN:
            dq.popleft()
        if dq:
            x = math.log(c[j] / c[dq[0]]) * sgn
            best = max(best, x)
        v = c[j] * sgn
        while dq and c[dq[-1]] * sgn >= v:
            dq.pop()
        dq.append(j)
    return best


def one(arg):
    ex, mk, d, sym, t0, t1, sub = arg
    t, h, l, c, q = ev.load_q(os.path.join(d, sym + ".csv.gz"), t0, t1)
    if len(t) < 100:
        return []
    cq = [0.0]
    for x in q:
        cq.append(cq[-1] + x)
    thu, thd = TH.get("%s|%d+|м" % (sub, TTH)), TH.get("%s|%d-|м" % (sub, TTH))
    if not thu or not thd:
        return []
    out = []
    for s, i0, i1 in zigzag(t, c, thu, thd):
        dur = (t[i1] - t[i0]) // MIN
        if dur < MIN_LEG:
            continue
        sgn = 1 if s == "+" else -1
        th = thu if s == "+" else thd
        move = math.log(c[i1] / c[i0]) * sgn
        bb = best_block(t, c, i0, i1, sgn)
        out.append({"биржа": ex, "рынок": mk, "sub": sub, "инструмент": sym, "монета": coin(sym), "знак": s,
                    "i0": i0, "i1": i1, "t_start_ms": t[i0], "t_peak_ms": t[i1], "длит_мин": dur,
                    "ход_%": round((math.exp(move) - 1) * 100 if s == "+" else (math.exp(-move) - 1) * 100, 3),
                    "θ": round(th, 5), "доля_в_блоке10": round(bb / move, 4) if move > 0 else None,
                    "E4": int(move - bb < th), "E1": int(bool(LEV.search(sym.split("-")[0]))),
                    "оборот_ноги": round(cq[i1 + 1] - cq[i0], 2), "минут_со_свечой": i1 - i0 + 1})
    return out


def main():
    global TTH
    root, thf, l4f, artf, outd = sys.argv[1:6]
    procs = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    ym = sys.argv[7] if len(sys.argv) > 7 else "2026-09"
    TTH = int(sys.argv[8]) if len(sys.argv) > 8 else 60
    mv.load_excl(artf)
    for k, v in json.load(open(thf))["результат"].items():
        TH[k] = v["x_star"] if isinstance(v, dict) else None
    for sub, R in json.load(open(l4f))["рынки"].items():
        for key, info in R.items():
            for a, b in info.get("период_список", []):
                PER.setdefault((sub, key[-1]), []).append((a, b))
    t0, t1 = mv.month_bounds(ym)
    os.makedirs(outd, exist_ok=True)
    rows = []
    with Pool(procs) as pool:
        for ex, mk, sub in mv.MKT:
            d = os.path.join(root, sub)
            syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
            n = 0
            for r in pool.imap_unordered(one, [(ex, mk, d, s, t0, t1, sub) for s in syms], chunksize=4):
                rows += r
                n += len(r)
            print(ex, mk, len(syms), "инструментов", n, "ног ≥ %d мин" % MIN_LEG, flush=True)
    for r in rows:
        r["E3"] = int(any(a <= r["t_peak_ms"] and b >= r["t_start_ms"] for a, b in PER.get((r["sub"], r["знак"]), [])))
        r["исключено"] = ",".join(k for k in ("E1", "E3", "E4") if r[k])
    rows.sort(key=lambda r: (r["sub"], r["инструмент"], r["t_start_ms"]))
    # пара: соседняя нога того же инструмента вверх→вниз, обе ≥ 90 мин (стык — пик первой = старт второй)
    by_ins = defaultdict(list)
    for r in rows:
        by_ins[(r["sub"], r["инструмент"])].append(r)
    for rs in by_ins.values():
        for r in rs:
            r["пара"] = "одиночная"
        for a, b in zip(rs, rs[1:]):
            if a["знак"] == "+" and b["знак"] == "-" and a["i1"] == b["i0"]:
                a["пара"], b["пара"] = "памп→дамп:памп", "памп→дамп:дамп"
    # кластер по монете: пересечение окон; представитель — наибольший оборот ноги (среди не исключённых, иначе любой)
    kept = [r for r in rows if not r["исключено"]]
    by_coin = defaultdict(list)
    for r in kept:
        by_coin[(r["монета"], r["знак"])].append(r)
    cl = 0
    for rs in by_coin.values():
        rs.sort(key=lambda r: r["t_start_ms"])
        cur, end = [], 0
        for r in rs + [None]:
            if r is None or (cur and r["t_start_ms"] > end):
                cl += 1
                rep = max(cur, key=lambda x: x["оборот_ноги"])
                for x in cur:
                    x["кластер"] = cl
                    x["представитель"] = int(x is rep)
                    x["рынков_в_кластере"] = len(cur)
                cur, end = [], 0
            if r is not None:
                cur.append(r)
                end = max(end, r["t_peak_ms"])
    cols = ["кластер", "представитель", "рынков_в_кластере", "биржа", "рынок", "инструмент", "монета", "знак", "пара",
            "t_start_ms", "t_peak_ms", "длит_мин", "ход_%", "θ", "доля_в_блоке10", "оборот_ноги", "минут_со_свечой",
            "E1", "E3", "E4", "исключено"]
    with open(os.path.join(outd, "legs-%s-th%d.csv" % (ym, TTH)), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    fun = {"ног ≥ %d мин" % MIN_LEG: len(rows), "после E1": sum(not r["E1"] for r in rows),
           "после E1,E3": sum(not (r["E1"] or r["E3"]) for r in rows), "после E1,E3,E4": len(kept),
           "кластеров (монета×знак)": cl, "представителей": sum(r.get("представитель", 0) for r in kept)}
    json.dump(fun, open(os.path.join(outd, "funnel-%s-th%d.json" % (ym, TTH)), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(fun, ensure_ascii=False))


if __name__ == "__main__":
    main()
