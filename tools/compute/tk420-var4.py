#!/usr/bin/env python3
"""TK-420 вар. 4, популяция A (Bybit linear, сентябрь): правила конца ноги/разворота E0–E3 по минутам окон после событий θs=5
(протокол docs/research/P-TK420-pump-dynamics-end-month2-2026-10-27.md §«Вар. 4», утверждён Судьёй). N=5 мин, h∈{5,15,60,240}.
Окно: от минуты события te (знак s) до первого из: онлайн-закрытие против хода ≥ θs=5 % от текущего экстремума хода; 120 мин после последнего события
того же знака внутри окна (оно продлевает окно); конец данных. События обратного знака внутри окна игнорируются (число пишется в stderr).
Кандидат-минута m (te < m ≤ конец окна), close[m] — закрытие минуты m из сделок мс (последняя цена; пустая минута — прежнее значение).
E0: экстремум хода с начала окна не обновлялся в минутах m−N+1..m. E0' = не E0. E1 = E0 ∧ surge_s(m) < нижнего тертиля базы
(surge_s = средняя за N мин сумма сделок СТОРОНЫ ХОДА в USD (покупки при s=+1) / медиана минутной суммы этой стороны по базе 360 мин; тертиль — по скользящим N-минутным значениям базы).
E2: dside(m) ≥ медианы базы, и в окне была минута < m с dside < нижнего тертиля базы (dside = bid-глубина 25 бп при s=+1, ask при s=−1; срезы концов минут tk414-obmin).
E3: E0' ∧ OI(5-мин бар минуты m) < OI(предыдущий бар). База = [te−минB−360 мин, te−минB) первого события окна (минB из obfeat-events.csv).
M_h = −s·(close[m+h]−close[m])/close[m]·1e4 (разворот); справочно MLast_h — то же от закрытия последней минуты окна.
Использование: tk420-var4.py <markout-A.csv> <obfeat-events.csv> <корень сделок bybit-linear> <корень срезов ob200/min> <каталог oi> <выход.csv> [процессов=15]"""
import csv, gzip, os, sys
from bisect import bisect_left
from datetime import datetime, timezone
from multiprocessing import Pool

N, TH, WMAX, HS = 5, 0.05, 120, (5, 15, 60, 240)
M0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()) // 60
NM = 31 * 1440


def tmin(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) // 60 - M0


def pct(vs, p):
    vs = sorted(vs); q = (len(vs) - 1) * p; k = int(q)
    return vs[k] + (vs[min(k + 1, len(vs) - 1)] - vs[k]) * (q - k)


def load_trades(root, sym):
    buy, sell, close = [0.0] * NM, [0.0] * NM, [0.0] * NM
    d = os.path.join(root, sym)
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with gzip.open(os.path.join(d, fn), "rt", newline="") as f:
            f.readline()
            for line in f:
                p = line.split(",")
                try:
                    m = int(float(p[0]) // 60) - M0
                    if 0 <= m < NM:
                        close[m] = float(p[4])
                        if p[2] == "Buy":
                            buy[m] += float(p[9])
                        else:
                            sell[m] += float(p[9])
                except (ValueError, IndexError):
                    pass
    last = 0.0
    for i in range(NM):
        if close[i] > 0:
            last = close[i]
        else:
            close[i] = last
    return buy, sell, close


def load_ob(root, sym):
    dB, dA = {}, {}
    d = os.path.join(root, sym)
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, fn)) as f:
            for line in f:
                p = line.split(",")
                m = int(p[0]) // 60 - M0
                dB[m] = float(p[3]); dA[m] = float(p[4])
    return dB, dA


def load_oi(root, sym):
    d = {}
    fn = os.path.join(root, sym + ".csv.gz")
    if os.path.exists(fn):
        with gzip.open(fn, "rt") as f:
            next(f)
            for line in f:
                p = line.split(",")
                d[int(p[0]) // 300000] = float(p[1])
    return d


def work(arg):
    troot, oroot, ioroot, sym, evs = arg
    buy, sell, close = load_trades(troot, sym)
    dB, dA = load_ob(oroot, sym)
    oi = load_oi(ioroot, sym)
    evs = sorted(evs, key=lambda e: e[0])
    rows, nwin, nopp, nstop = [], 0, 0, 0
    i = 0
    while i < len(evs):
        te, s, mb = evs[i]
        i += 1
        if te < 1 or te >= NM - 1 or close[te] <= 0:
            continue
        side = buy if s == 1 else sell
        # база: срезы и поток стороны
        b0, b1 = te - mb - 360, te - mb
        if b0 < 0:
            continue
        sv = side[b0:b1]
        smed = sorted(sv)[len(sv) // 2]
        roll = [sum(sv[j:j + N]) / N / smed for j in range(len(sv) - N + 1)] if smed > 0 else []
        dd = dB if s == 1 else dA
        bd = [dd[m] for m in range(b0, b1) if m in dd]
        if len(roll) < 60 or len(bd) < 60:
            continue
        thr_s = pct(roll, 1 / 3)
        thr_d_lo, thr_d_med = pct(bd, 1 / 3), pct(bd, 0.5)
        # окно
        ext, ext_m, end, last_ev, stopped = close[te], te, None, te, False
        m = te
        cand = []
        while True:
            m += 1
            if m >= NM:
                break
            while i < len(evs) and evs[i][0] <= m:
                if evs[i][1] == s:
                    last_ev = evs[i][0]
                else:
                    nopp += 1
                i += 1
            c = close[m]
            if s * (c - ext) > 0:
                ext, ext_m = c, m
            if s * (ext - c) / ext >= TH:
                stopped = True
                end = m
                break
            if m - last_ev > WMAX:
                end = m - 1
                break
            cand.append((m, ext_m))
            end = m
        if not cand:
            continue
        nwin += 1
        nstop += stopped
        day = (te + M0) * 60 // 86400
        low_seen = False
        out = []
        for (m, em) in cand:
            e0 = em <= m - N
            sur = sum(side[m - N + 1:m + 1]) / N / smed if smed > 0 else None
            e1 = e0 and sur is not None and sur < thr_s
            dm = dd.get(m)
            e2 = dm is not None and low_seen and dm >= thr_d_med
            if dm is not None and dm < thr_d_lo:
                low_seen = True
            be = ((m + M0) * 60) // 300
            e3 = None
            if be in oi and be - 1 in oi and oi[be - 1] > 0:
                e3 = (not e0) and oi[be] < oi[be - 1]
            r = dict(инструмент=sym, окно=nwin, день=day, знак=s, m=m + M0, пик_m=em + M0, E0=int(e0), E1=int(e1), E2=int(e2),
                     E3="" if e3 is None else int(e3), окно_остановлено=int(stopped))
            for h in HS:
                r["M%d" % h] = "%.3f" % (-s * (close[m + h] - close[m]) / close[m] * 1e4) if m + h < NM and close[m + h] > 0 and m + h <= (NM - 1) else ""
            out.append(r)
        # контроль: от закрытия последней минуты окна
        lm = cand[-1][0]
        for r in out:
            for h in HS:
                r["MLast%d" % h] = "%.3f" % (-s * (close[lm + h] - close[lm]) / close[lm] * 1e4) if lm + h < NM and close[lm + h] > 0 else ""
        rows.extend(out)
    return rows, (sym, nwin, nstop, nopp)


def main():
    mo_f, ob_f, troot, oroot, ioroot, outp = sys.argv[1:7]
    nproc = int(sys.argv[7]) if len(sys.argv) > 7 else 15
    minb = {}
    for r in csv.DictReader(open(ob_f, newline="", encoding="utf-8")):
        minb[(r["инструмент"], r["t_событие"], r["знак"])] = int(r["минB"])
    by, nm = {}, 0
    for r in csv.DictReader(open(mo_f, newline="", encoding="utf-8")):
        mb = minb.get((r["инструмент"], r["t_событие"], r["знак"]))
        if mb is None:
            nm += 1; mb = 0
        by.setdefault(r["инструмент"], []).append((tmin(r["t_событие"]), int(r["знак"]), mb))
    print("событий без минB:", nm, file=sys.stderr)
    cols = None
    with Pool(nproc) as p, open(outp, "w", newline="", encoding="utf-8") as f:
        w = None
        for rows, info in p.imap_unordered(work, [(troot, oroot, ioroot, k, v) for k, v in sorted(by.items())]):
            print("инстр %s окон %d остановлено %d обр.знак_внутри %d строк %d" % (info + (len(rows),)), file=sys.stderr, flush=True)
            if rows and w is None:
                w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader()
            if rows:
                w.writerows(rows)


if __name__ == "__main__":
    main()
