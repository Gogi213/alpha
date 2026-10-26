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
[начало, пик] пересекается с нашим; лаг = старт другого − наш старт (мин; > 0 — мы раньше).
Плохой принт (решение Судьи 27.10, В-53): ход эпизода по VWAP минуты (quote_v / base_v; экстремум VWAP на [начало, пик] к VWAP
начала) < 0,5 × ход по закрытиям → плохой_принт=да (из сводок исключается, в csv остаётся). ход_пары_доля — наибольший
из ходов того же знака на других инструментах той же монеты в окне [начало, пик] ± 5 мин, делённый на наш ход.
Файлы Bybit без n_trades (пустая колонка) читаются с n=0; пустой загруз файла с данными — ошибка."""
import csv
import gzip
import os
import statistics
import sys
from bisect import bisect_left, bisect_right
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
        "лаг_пика_мин", "первый", "перекрытие_доля", "сомнительный", "ход_vwap_доля", "плохой_принт", "ход_пары_доля"]


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")


def load(root, ex, mk, sym):
    t, c, q, n, w = [], [], [], [], []
    rows = 0
    with gzip.open(os.path.join(root, MKT[(ex, mk)], sym + ".csv.gz"), "rt") as f:
        f.readline()
        for line in f:
            rows += 1
            p = line.rstrip("\n").split(",")
            ti = int(p[0]); ci = float(p[4]); vi = float(p[5]); qi = float(p[6])
            ni = int(float(p[7])) if len(p) > 7 and p[7] else 0  # Bybit-API n_trades не отдаёт
            if ci <= 0:
                continue
            t.append(ti); c.append(ci); q.append(qi); n.append(ni); w.append(qi / vi if vi > 0 and qi > 0 else 0.0)
    if rows and not c:
        raise RuntimeError("пустой загруз %s %s %s: %d строк, 0 годных" % (ex, mk, sym, rows))
    return t, c, q, n, w


def despike(t, c, q, n, w):
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
        return t, c, q, n, w, 0
    idx = [i for i in range(len(c)) if keep[i]]
    return [t[i] for i in idx], [c[i] for i in idx], [q[i] for i in idx], [n[i] for i in idx], [w[i] for i in idx], bad


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


def episodes(t, c, q, n, w, th, medday):
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
        vs = [x for x in w[a:b + 1] if x > 0]
        vm = ""
        if vs and w[a] > 0 and move != 0:
            vm = ((max(vs) if s == 1 else min(vs)) / w[a] - 1) / move
        gap = max((t[j] - t[j - 1]) / 60000.0 for j in range(a + 1, b + 1)) if b > a else 0.0
        out.append(dict(s=s, a=a, b=b, e=e, move=move * 100, dur=dur, retr=retr,
                        tretr=(t[e] - t[b]) / 60000.0, done=done and k + 1 < len(legs), qe=qe, norm=norm,
                        ratio=(qe / norm) if norm else "", mins=b - a + 1, trades=sum(n[a:b + 1]), gap=gap, vm=vm))
    return out


def pair_share(data, others, s, ts, tp, move):
    """Наибольший ход того же знака у других инструментов монеты в окне [ts-5м, tp+5м], доля от нашего хода."""
    best = None
    for o in others:
        to, co = data[o][0], data[o][1]
        i, j = bisect_left(to, ts - 300000), bisect_right(to, tp + 300000)
        if j - i < 2:
            continue
        ext, m = co[i], 0.0
        for x in co[i + 1:j]:
            if s == 1:
                ext = min(ext, x); m = max(m, x / ext - 1)
            else:
                ext = max(ext, x); m = max(m, 1 - x / ext)
        if best is None or m > best:
            best = m
    return round(best * 100 / abs(move), 2) if best is not None and move else ""


def run_group(args):
    root, key, sel, allk = args
    data = {}
    for ex, mk, sym in allk:
        t, c, q, n, w = load(root, ex, mk, sym)
        if not c:
            continue
        days = {}
        for ti, qi in zip(t, q):
            days[(ti - 1788220800000) // DAY] = days.get((ti - 1788220800000) // DAY, 0.0) + qi
        t, c, q, n, w, bad = despike(t, c, q, n, w)
        data[(ex, mk, sym)] = (t, c, q, n, bad, statistics.median(days.values()) if days else 0.0, w)
    rows = []
    diag = {k: (len(data[k][1]), round((max(data[k][1]) / min(data[k][1]) - 1) * 100, 1) if k in data else "") for k in sel if k in data}
    diag.update({k: (0, "") for k in sel if k not in data})
    for th in THETAS:
        eps = {k: episodes(v[0], v[1], v[2], v[3], v[6], th, v[5]) for k, v in data.items()}
        for k in sel:
            if k not in data:
                continue
            t, c, q, n, bad, medday, _w = data[k]
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
                             round(e["gap"], 1), round(medday, 0), bad, len(others)] + sync[:5] + [sync[5]] + ["да" if e["mins"] <= 3 else "нет"]
                             + [round(e["vm"], 2) if e["vm"] != "" else "", "да" if e["vm"] != "" and e["vm"] < 0.5 else "нет",
                                pair_share(data, others, e["s"], ts, tp, e["move"]) if others else ""])
    return rows, diag


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
        for rs, _ in res:
            w.writerows(rs)
    have = {(r[1], r[2], r[3]) for rs, _ in res for r in rs if r[0] == 5.0}
    allsel = {k: d for _, dg in res for k, d in dg.items()}
    miss = [k for k in allsel if k not in have]
    with open(out + ".noep.tsv", "w", encoding="utf-8") as f:
        f.write("биржа\tрынок\tинструмент\tминут\tмакс/мин_закрытий_%\n")
        for k in sorted(miss):
            f.write("%s\t%s\t%s\t%s\t%s\n" % (k + allsel[k]))
    print("эпизодов", sum(len(r) for r, _ in res), "инструментов отбора", len(allsel), "без эпизода при θ=5:", len(miss))
