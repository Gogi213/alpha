#!/usr/bin/env python3
"""TK-414 возврат Судьи 27.10 (детектор общих точек). Эталон — зигзаг TK-413 θ=10,48 % по закрытиям 1-м свечей (та же despike/zigzag).
Онлайн-правило — тот же зигзаг с малым порогом θs, только по прошлому: событие (tf, знак s) = цена ушла на θs от текущего экстремума
(это и «нога знака s началась» = подтверждение разворота предыдущей ноги); оценка старта/пика = экстремум на момент события (известен онлайн).
Пороги заморожены до счёта: θs = 5 % (главный), 3,5 и 7 % — только чувствительность. Вариант «половина» (θs=5 %, разворот ноги
подтверждается откатом max(5 %, 0,5 × ход с оценки старта)) — второй вариант разворота; меряется на тех же ногах.
Точки на эталонную ногу знака s [t0,t1]: (1) D — первое событие знака s с tf≥t0: запаздывание мин и доля хода (c[tf]-c[t0])/(c[t1]-c[t0]);
(2) ошибка оценки старта = |t_оценка − t0| мин; (3) пик/конец: оценка пика при первом событии знака −s с tf≥t1, ошибка |t_оценка − t1|;
(4) разворот: запаздывание tf_r − t1 (мин) и доля хода, отданная к tf_r; (5) пауза: события знака −s внутри ноги (D<tf<t1) = ложные развороты;
возобновление — следующее событие знака s после паузы: мин от дна паузы до подтверждения.
Ложные срабатывания: события знака s вне любой ноги эталона того же знака (по всем инструментам охвата), на инструмент-сутки.
Ноги эталона с плохим принтом/сомнительные (episodes csv) — из статистики и из ложных срабатываний исключены (окно ноги).
Использование: tk414-detector.py <корень свечей> <coverage.csv> <episodes.csv> <volatile.csv> <legs.csv> <выход-префикс> [процессов=15]"""
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

THETA = 0.1048
VARIANTS = {"5": (0.05, False), "3.5": (0.035, False), "7": (0.07, False), "5half": (0.05, True)}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")


def online(c, th, half):
    """События онлайн-зигзага: (i_подтверждения, знак, i_экстремума_старта_новой_ноги). Для half: разворот восходящей/нисходящей ноги
    подтверждается откатом max(th, 0,5×ход с оценки старта) вместо th."""
    ev = []
    n = len(c)
    if n < 2:
        return ev
    hi = lo = 0
    d = 0
    start = 0
    for i in range(1, n):
        x = c[i]
        if d == 0:
            if x > c[hi]:
                hi = i
            if x < c[lo]:
                lo = i
            if x >= c[lo] * (1 + th) and lo < i:
                d = 1; start = lo; hi = i; ev.append((i, 1, lo))
            elif x <= c[hi] * (1 - th) and hi < i:
                d = -1; start = hi; lo = i; ev.append((i, -1, hi))
        elif d == 1:
            if x > c[hi]:
                hi = i
                continue
            need = th
            if half:
                need = max(th, 0.5 * (c[hi] / c[start] - 1))
            if x <= c[hi] * (1 - need):
                ev.append((i, -1, hi)); start = hi; d = -1; lo = i
        else:
            if x < c[lo]:
                lo = i
                continue
            need = th
            if half:
                need = max(th, 0.5 * (1 - c[lo] / c[start]))
            if x >= c[lo] * (1 + need):
                ev.append((i, 1, lo)); start = lo; d = 1; hi = i
    return ev


def work(arg):
    root, ex, mk, sym, bad, vol = arg
    try:
        t, c, q, n, w = T13.load(root, ex, mk, sym)
    except FileNotFoundError:
        return [], [], None
    t, c, q, n, w, _ = T13.despike(t, c, q, n, w)
    if len(c) < 100:
        return [], [], None
    days = len({x // 86400000 for x in t})
    legs = T13.zigzag(c, THETA)
    rows, fps = [], []
    for var, (th, half) in VARIANTS.items():
        ev = online(c, th, half)
        # индекс событий по знаку
        evt = [(e[0], e[1], e[2]) for e in ev]
        inside = {1: [], -1: []}
        for (a, b, s, done) in legs:
            key = (ex, mk, sym, "памп" if s == 1 else "дамп", iso(t[a]))
            inside[s].append((a, b, key in bad))
        # ложные срабатывания: события знака s вне ног знака s (исключая окна плохих ног любого знака)
        fp = tot = 0
        for (i, s, ext) in evt:
            hit = None
            for (a, b, bd) in inside[s]:
                if a <= i <= b:
                    hit = bd
                    break
            anybad = any(bd and a <= i <= b for s2 in (1, -1) for (a, b, bd) in inside[s2])
            if anybad:
                continue
            tot += 1
            if hit is None:
                fp += 1
        fps.append(dict(вариант=var, биржа=ex, рынок=mk, инструмент=sym, волатильный=vol, дней=days, событий=tot, ложных=fp))
        if var == "3.5" or var == "7":
            pass
        for (a, b, s, done) in legs:
            key = (ex, mk, sym, "памп" if s == 1 else "дамп", iso(t[a]))
            if key in bad or not done:
                continue
            p0, p1 = c[a], c[b]
            mv = p1 - p0
            if mv == 0:
                continue
            row = dict(вариант=var, биржа=ex, рынок=mk, инструмент=sym, знак="памп" if s == 1 else "дамп", начало_utc=iso(t[a]),
                       пик_utc=iso(t[b]), ход_pct=round((p1 / p0 - 1) * 100, 2), длит_мин=(t[b] - t[a]) / 60000.0)
            dev = [e for e in evt if e[1] == s and e[0] >= a]
            if dev and dev[0][0] <= b:
                i, _, ext = dev[0]
                row["D_лаг_мин"] = (t[i] - t[a]) / 60000.0
                row["D_доля_хода"] = (c[i] - p0) / mv
                row["старт_ошибка_мин"] = abs(t[ext] - t[a]) / 60000.0
                d_i = i
            else:
                d_i = None
            rev = [e for e in evt if e[1] == -s and e[0] >= b]
            if rev:
                i, _, ext = rev[0]
                row["разворот_лаг_мин"] = (t[i] - t[b]) / 60000.0
                row["разворот_отдано_доля"] = (p1 - c[i]) / mv
                row["пик_ошибка_мин"] = abs(t[ext] - t[b]) / 60000.0
            # ложные развороты внутри ноги
            fr = [e for e in evt if e[1] == -s and d_i is not None and d_i < e[0] < b]
            row["пауз_ложных"] = len(fr)
            if fr:
                res = []
                for (i, _, ext) in fr:
                    up = [e for e in evt if e[1] == s and e[0] > i]
                    if up and up[0][0] <= b:
                        res.append((t[up[0][0]] - t[ext]) / 60000.0)
                if res:
                    row["возобн_мин_от_дна"] = sorted(res)[len(res) // 2]
            rows.append(row)
    return rows, fps, None


def main():
    root, cov, epf, volf, legsf, outp = sys.argv[1:7]
    nproc = int(sys.argv[7]) if len(sys.argv) > 7 else 15
    bad = set()
    with open(epf, newline="") as f:
        for r in csv.DictReader(f):
            if r["порог_%"] == "10.48" and (r["плохой_принт"] == "да" or r["сомнительный"] == "да"):
                bad.add((r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"]))
    volset = set()
    with open(volf, newline="") as f:
        for r in csv.DictReader(f):
            if r["тип"] == "монета" and r["котировка_usd"] == "да" and int(r["дней_с_данными"]) >= 10 and float(r["диапазон_дня_мед_%"]) >= 10.48:
                volset.add((r["биржа"], r["рынок"], r["инструмент"]))  # правило отбора Судьи TK-412
    forms = {}
    with open(legsf, newline="") as f:
        for r in csv.DictReader(f):
            forms[(r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"])] = r["форма"]
    jobs = []
    with open(cov, newline="") as f:
        for r in csv.DictReader(f):
            k = (r["биржа"], r["рынок"], r["инструмент"])
            jobs.append((root, k[0], k[1], k[2], bad, 1 if k in volset else 0))
    print("инструментов", len(jobs), flush=True)
    allrows, allfp = [], []
    with Pool(nproc) as pool:
        for rows, fps, _ in pool.imap_unordered(work, jobs, chunksize=4):
            allrows += rows
            allfp += fps
    for r in allrows:
        r["форма"] = forms.get((r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"]), "")
    cols = ["вариант", "биржа", "рынок", "инструмент", "знак", "начало_utc", "пик_utc", "ход_pct", "длит_мин", "форма", "D_лаг_мин",
            "D_доля_хода", "старт_ошибка_мин", "разворот_лаг_мин", "разворот_отдано_доля", "пик_ошибка_мин", "пауз_ложных",
            "возобн_мин_от_дна"]
    with open(outp + "-legs.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(allrows)
    with open(outp + "-fp.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, ["вариант", "биржа", "рынок", "инструмент", "волатильный", "дней", "событий", "ложных"])
        wr.writeheader()
        wr.writerows(allfp)
    print("ног", len(allrows), "инструментов с fp", len(allfp) // len(VARIANTS), flush=True)


if __name__ == "__main__":
    main()
