#!/usr/bin/env python3
"""TK-413: памп/дамп-эпизоды сентября у волатильных монет (свечи 1 м TK-411, отбор TK-412).
Использование: tk413-episodes.py <корень свечей> <volatile-coins.csv> <выход.csv> [процессов] [макс. монет]
Отбор инструментов — правило Судьи TK-412: тип=монета, котировка_usd=да, дней≥10, диапазон_дня_мед_%≥10.48.
Эпизод = нога зигзага по ЗАКРЫТИЯМ 1-м свечей с порогом разворота θ: памп — от впадины до пика, дамп — от пика до дна,
нога кончается, когда цена развернулась на θ от экстремума (поэтому каждая нога ≥ θ). Три порога θ — чувствительность.
Плохие принты: закрытие-одиночный выброс (за обоими соседями) глубиной ≥ 5 % (наименьший θ), вернувшийся
на ≥ половину назад к соседям, выбрасывается до зигзага (число выброшенных пишется). Время — UTC.
Откат = доля хода, отыгранная до следующего разворота (конец эпизода = время следующего разворота).
Оборот эпизода = Σ quote_v по минутам от начала до пика; норма = медиана дневного оборота × длительность/1440.
Сомнительный = эпизод держится на ≤ 3 минутах с торгами (остаток плохих принтов Gate).
Синхрон: эпизод того же знака на другом инструменте той же монеты (любая биржа/рынок, USD-котировка), чей интервал
[начало, пик] пересекается с нашим; лаг = старт другого − наш старт (мин; > 0 — мы раньше)."""
import csv
import gzip
import os
import statistics
import sys
from datetime import datetime, timezone
from multiprocessing import Pool

THETAS = (0.05, 0.1048, 0.20)
SPIKE = 0.05
MKT = {("bybit", "linear"): "bybit-linear", ("bybit", "spot"): "bybit-spot",
       ("gate", "futures_usdt"): "gate-futures_usdt", ("gate", "spot"): "gate-spot"}
DAY = 86400000
HEAD = ["порог_%", "биржа", "рынок", "инструмент", "ключ_монеты", "знак", "начало_utc", "пик_utc", "конец_utc",
        "цена_начала", "цена_пика", "ход_%", "длит_до_пика_мин", "откат_доля", "время_отката_мин", "завершён",
        "оборот_эпизода_usd", "норма_оборота_usd", "оборот_к_норме", "минут_с_торгами", "сделок", "макс_дыра_мин",
        "мед_оборот_дня_usd", "выброшено_принтов", "есть_пара_монеты", "синхрон", "пара_инструмент", "лаг_старта_мин",
        "лаг_пика_мин", "первый", "перекрытие_доля", "сомнительный"]


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")


def load(root, ex, mk, sym):
    t, c, q, n = [], [], [], []
    with gzip.open(os.path.join(root, MKT[(ex, mk)], sym + ".csv.gz"), "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",")
            try:
                ti = int(p[0]); ci = float(p[4]); qi = float(p[6]); ni = int(float(p[7]))
            except (ValueError, IndexError):
                continue
            if ci <= 0:
                continue
            t.append(ti); c.append(ci); q.append(qi); n.append(ni)
    return t, c, q, n


def despike(t, c, q, n):
    """Выбросить одиночные закрытия-выбросы (см. docstring); вернуть списки и число выброшенных."""
    keep = [True] * len(c)
    bad = 0
    for i in range(1, len(c) - 1):
        a, b, d = c[i - 1], c[i], c[i + 1]
        if (b > a and b > d) or (b < a and b < d):
            dev = min(abs(b - a) / a, abs(d - b) / b)
            if dev >= SPIKE and abs(d - a) / a <= dev / 2:
                keep[i] = False
                bad += 1
    if not bad:
        return t, c, q, n, 0
    idx = [i for i in range(len(c)) if keep[i]]
    return [t[i] for i in idx], [c[i] for i in idx], [q[i] for i in idx], [n[i] for i in idx], bad


def zigzag(c, th):
    """Ноги зигзага: (i_нач, i_кон, знак, завершён). Индексы — позиции в массиве."""
    legs = []
    if not c:
        return legs
    hi = lo = 0
    d = 0
    start = 0
    for i in range(1, len(c)):
        x = c[i]
        if d == 0:
            if x > c[hi]:
                hi = i
            if x < c[lo]:
                lo = i
            if x >= c[lo] * (1 + th) and lo < i:
                d = 1; start = lo; hi = i
            elif x <= c[hi] * (1 - th) and hi < i:
                d = -1; start = hi; lo = i
        elif d == 1:
            if x > c[hi]:
                hi = i
            elif x <= c[hi] * (1 - th):
                legs.append((start, hi, 1, True))
                start = hi; d = -1; lo = i
        else:
            if x < c[lo]:
                lo = i
            elif x >= c[lo] * (1 + th):
                legs.append((start, lo, -1, True))
                start = lo; d = 1; hi = i
    if d == 1 and c[hi] >= c[start] * (1 + th):
        legs.append((start, hi, 1, False))
    elif d == -1 and c[lo] <= c[start] * (1 - th):
        legs.append((start, lo, -1, False))
    return legs


def episodes(t, c, q, n, th, medday):
    legs = zigzag(c, th)
    out = []
    for k, (a, b, s, done) in enumerate(legs):
        move = c[b] / c[a] - 1
        # следующий разворот: конец следующей ноги; у последней — экстремум противоположной стороны до конца данных
        if k + 1 < len(legs):
            e = legs[k + 1][1]
        else:
            seg = c[b:]
            e = b + (seg.index(min(seg)) if s == 1 else seg.index(max(seg)))
        retr = (c[b] - c[e]) / (c[b] - c[a]) if c[b] != c[a] else 0.0
        qe = sum(q[a:b + 1])
        dur = (t[b] - t[a]) / 60000.0
        norm = medday * dur / 1440.0 if medday else 0.0
        gap = max((t[j] - t[j - 1]) / 60000.0 for j in range(a + 1, b + 1)) if b > a else 0.0
        out.append(dict(s=s, a=a, b=b, e=e, move=move * 100, dur=dur, retr=retr,
                        tretr=(t[e] - t[b]) / 60000.0, done=done and k + 1 < len(legs), qe=qe, norm=norm,
                        ratio=(qe / norm) if norm else "", mins=b - a + 1, trades=sum(n[a:b + 1]), gap=gap))
    return out


def run_group(args):
    root, key, sel, allk = args
    data = {}
    for ex, mk, sym in allk:
        t, c, q, n = load(root, ex, mk, sym)
        if not c:
            continue
        days = {}
        for ti, qi in zip(t, q):
            days[(ti - 1788220800000) // DAY] = days.get((ti - 1788220800000) // DAY, 0.0) + qi
        t, c, q, n, bad = despike(t, c, q, n)
        data[(ex, mk, sym)] = (t, c, q, n, bad, statistics.median(days.values()) if days else 0.0)
    rows = []
    for th in THETAS:
        eps = {k: episodes(v[0], v[1], v[2], v[3], th, v[5]) for k, v in data.items()}
        for k in sel:
            if k not in data:
                continue
            t, c, q, n, bad, medday = data[k]
            others = [o for o in data if o != k]
            for e in eps[k]:
                ts, tp, te = t[e["a"]], t[e["b"]], t[e["e"]]
                best = None
                for o in others:
                    to = data[o][0]
                    for f in eps[o]:
                        if f["s"] != e["s"]:
                            continue
                        os_, op = to[f["a"]], to[f["b"]]
                        ov = min(tp, op) - max(ts, os_)
                        if ov >= 0 and (best is None or ov > best[0]):
                            sh = min(tp - ts, op - os_)
                            best = (ov, o, os_, op, round(ov / sh, 2) if sh > 0 else 1.0)
                if best:
                    sync = ["да", "/".join(best[1][:2]) + ":" + best[1][2], round((best[2] - ts) / 60000.0, 1),
                            round((best[3] - tp) / 60000.0, 1),
                            "мы" if best[2] > ts else ("пара" if best[2] < ts else "одновременно"),
                            best[4]]
                else:
                    sync = ["нет", "", "", "", "", ""]
                rows.append([round(th * 100, 2), k[0], k[1], k[2], key, "памп" if e["s"] == 1 else "дамп",
                             iso(ts), iso(tp), iso(te), c[e["a"]], c[e["b"]], round(e["move"], 2), round(e["dur"], 1),
                             round(e["retr"], 3), round(e["tretr"], 1), "да" if e["done"] else "нет",
                             round(e["qe"], 0), round(e["norm"], 0),
                             round(e["ratio"], 2) if e["ratio"] != "" else "", e["mins"], e["trades"],
                             round(e["gap"], 1), round(medday, 0), bad, len(others)] + sync[:5] + [sync[5]] + ["да" if e["mins"] <= 3 else "нет"])
    return rows


if __name__ == "__main__":
    root, vol, out = sys.argv[1:4]
    nproc = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    lim = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    inst = list(csv.DictReader(open(vol, encoding="utf-8")))
    sel, allk = {}, {}
    for r in inst:
        if r["тип"] != "монета" or r["котировка_usd"] != "да" or not r["дней_с_данными"] or int(r["дней_с_данными"]) < 1:
            continue
        k = (r["биржа"], r["рынок"], r["инструмент"])
        allk.setdefault(r["ключ_монеты"], []).append(k)
        if int(r["дней_с_данными"]) >= 10 and float(r["диапазон_дня_мед_%"]) >= 10.48:
            sel.setdefault(r["ключ_монеты"], []).append(k)
    keys = sorted(sel)
    if lim:
        keys = keys[:lim]
    print("монет", len(keys), "инструментов отбора", sum(len(sel[k]) for k in keys), flush=True)
    jobs = [(root, k, sel[k], allk[k]) for k in keys]
    with Pool(nproc) as pool:
        res = pool.map(run_group, jobs, chunksize=1)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for rs in res:
            w.writerows(rs)
    print("эпизодов", sum(len(r) for r in res))
