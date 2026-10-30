#!/usr/bin/env python3
"""TK-461/TK-466 / P-TK459, P-TK461 §12.2: слой S -> входной csv ядра TK-463. Исходов не считает.
python tk461_s_events.py <s1|s2|s3|s4|s5> <d2b-ev каталог (s3 берёт из него только список монет)> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
События S1/S2/S4 = события A5 (S3 — свои события sm_s3: A6, a8=слом, якоря k=2/k=1) (свип k=2, 1H/4H/1D) из d2b-ev TK-461 (там уже side, t0, tt0, atr_bps, ss_bps, ot1_bps, ot1_fallback);
слой S добавляет только флаги фактора bp (читаются по данным <= момента свипа, без исходов), строка не теряется: подмножество
«с фактором»/«без фактора» ядро берёт по флагу. Флаги — тем же кодом, что принятые sm_s1/sm_s2/sm_s4 (импорт, логика не переписана).
  s1 (1H): asia/frankfurt/london/ny = час свипа по Нью-Йорку (S1.SES), 0/1.
  s2 (1H/4H/1D): pa, pb = положение в диапазоне (G2, ТФ свипа / старший), prem_a, prem_b = p>0,5 (NaN если p NaN или p==0,5).
  s5 (1H): s5 = час свипа = час первого снятия границы Азии в ту же сторону (с контрольными сдвигами G5), s5x = исключить.
  s4 (1H/4H/1D): pD1, pW1 (1D — только pW1) = 1, если последний подтверждённый фрактал старшего ТФ противоположного типа (NaN — нет)."""
import importlib.util
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, ".."), "/data/tk0451"):
    if os.path.isdir(p):
        sys.path.insert(0, p)


def _load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(HERE, "..", n + ".py"))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def s1_flags(ev, tf, path):
    h = pd.to_datetime(ev.tj.values, unit="ms", utc=True).tz_convert("America/New_York").hour.values
    S1 = _load("sm_s1")
    out = ev.copy()
    for s, f in S1.SES.items():
        out[s] = f(h).astype(int)
    return out


def s2_flags(ev, tf, path):
    S2 = _load("sm_s2")
    A = S2.bars(path, tf)
    tf2 = S2.TFS[tf][1]
    B = S2.bars(path, tf2)
    b0 = B.index.min()
    out = ev.copy()
    pa = np.full(len(ev), np.nan)
    pb = np.full(len(ev), np.nan)
    for dr in ("H", "L"):
        sg = 1 if dr == "H" else -1

        def mir(X):
            return ((X.h.values.astype(float), X.l.values.astype(float)) if sg == 1
                    else (-X.l.values.astype(float), -X.h.values.astype(float)))
        hA, lA = mir(A)
        hB, lB = mir(B)
        shA, slA = S2.legs(hA, lA, A.ok.values)
        shB, slB = S2.legs(hB, lB, B.ok.values)
        for n in np.flatnonzero(ev.dir.values == dr):
            j, i = int(ev.j.values[n]), int(ev.i.values[n])
            lvl, hj = hA[i], hA[j]
            pa[n] = S2.pos_flag(hA, lA, shA, slA, j - 1, hj, lvl)
            pbar = int(ev.tj.values[n] // (tf2 * 60000) - b0) - 1
            if pbar >= 0:
                pb[n] = S2.pos_flag(hB, lB, shB, slB, pbar, hj, lvl)
    out["pa"], out["pb"] = pa, pb
    for v in ("a", "b"):
        p = out["p" + v]
        out["prem_" + v] = np.where(p.isna() | (p == 0.5), np.nan, (p > 0.5).astype(float))
    return out


def s4_flags(ev, tf, path):
    S4 = _load("sm_s4")
    out = ev.copy()
    for t2 in S4.TFS[tf][1]:
        ct, ty = S4.fractals(path, t2)
        k = np.searchsorted(ct, ev.tj.values, side="right") - 1
        last = np.where(k >= 0, ty[np.maximum(k, 0)], 0)
        want = np.where(ev.dir.values == "H", -1, 1)
        out["p" + S4.HN[t2]] = np.where(last == 0, np.nan, (last == want).astype(float))
    return out


def s3_events(path, sym):
    """S3 (sm_s3.one, правила событий слово в слово, исходов нет): свип без пробоя + первая щель g>=GMIN в окне K_I + слом в окне K_B (fl=1 -> A8, fl=0 -> A6 без A8);
    момент знания t0 = max(закрытие щели, бар слома), вход по закрытию бара t0 (как sig_table: tt0 = открытие бара t0), сторона против свипа (H -> шорт),
    стоп = дальняя граница щели: ss_bps = (far - c[t0])/|c[t0]|*1e4 > 0. Якоря k=2 (Санчо) и k=1 (bp) -> колонка anchor. Исход (fulfill/инвалидация) не пишется."""
    S3 = _load("sm_s3")
    KI, KB, GMIN, N = S3.KI, S3.KB, S3.GMIN, S3.N
    out = []
    for tf, tfn in S3.TFS.items():
        a = S3.bars(path, tf); ok = a.ok.values; tms = a.t.values
        for k in S3.VARS:
            for dname, sgn in (("H", 1), ("L", -1)):
                if sgn == 1:
                    h, l, c = (a[x].values.astype(float) for x in "hlc")
                else:
                    h, l, c = -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
                n = len(h)
                atr = S3.atr14(h, l, c)
                sh, sl = S3.swings(h, ok, k), S3.swings(-l, ok, k)
                slc = sl + k
                okc = np.r_[0, np.cumsum(ok)]
                for i in sh:
                    L = h[i]
                    s = i + k + 1
                    hit = np.flatnonzero(h[s:] > L)
                    if not len(hit):
                        continue
                    j = s + hit[0]
                    if not (ok[j] and ok[j - 1]) or np.isnan(atr[j - 1]) or c[j] > L:
                        continue
                    A = atr[j - 1]
                    if j + KI >= n or okc[j + KI] - okc[j] != KI:
                        continue
                    m = np.searchsorted(slc, j, side="right")
                    if m == 0:
                        continue
                    last = l[sl[m - 1]]
                    g = (l[j:j + KI - 2] - h[j + 2:j + KI]) / A
                    q = np.flatnonzero(g >= GMIN)
                    if not len(q):
                        continue
                    q = j + int(q[0])
                    bb = np.flatnonzero(c[j + 1:j + 1 + KB] < last)
                    fl = int(len(bb) > 0)
                    t0 = max(q + 2, j + 1 + int(bb[0])) if fl else q + 2
                    if t0 + N >= n or okc[t0 + N + 1] - okc[q] != t0 + N - q + 1:
                        continue
                    d = l[q] - c[t0]
                    if d <= 0:
                        continue
                    out.append(dict(sym=sym, tf=tf, ev="S3", dir=dname, side=-sgn, anchor=k, i=int(i), j=int(j), t0=int(t0), tt0=int(tms[t0]), a8=fl,
                                    atr_bps=atr[t0] / abs(c[t0]) * 1e4, ss_bps=d / abs(c[t0]) * 1e4))
    o = pd.DataFrame(out)
    if len(o):
        o["ot1_bps"] = o.ss_bps
        o["ot1_fallback"] = 0
    return o


def s5_removals(path):
    """sm_s5.coin (цикл по суткам, one2, контрольные сдвиги) без исходов -> (множество (сторона, час-бар) первого снятия границы Азии, множество «x»:
    противоположная граница касалась до закрытия часа - исключаются из обеих групп S5)."""
    import datetime as dt
    S5 = _load("sm_s5")
    G5 = S5.G5
    m0, h, l = G5.minutes(path)
    rem, excl = set(), set()
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=G5.NY).astimezone(G5.UTC).timestamp() // 60)
        s = s0 - m0
        r = [S5.one2(h, l, s + 60 * k, m0) for k in G5.SHIFTS]
        if r[0] is None or r[0][0] == 0:
            continue
        if r[0][0] == "x":
            excl.add((r[0][1], r[0][2] // 60))
            continue
        if any(x is not None and x[0] == 1 for x in r[1:]):
            rem.add((r[0][1], r[0][2] // 60))
    return rem, excl


def s5_flags(ev, tf, path):
    """s5 (1H): s5 = 1, если час-бар свипа A5 совпадает с часом первого снятия границы Азии в ту же сторону (G5, окно 02–11 NY); s5x = 1 — событие исключается
    (противоположная граница касалась до закрытия часа). Остальные A5 1H — «без фактора»."""
    rem, excl = s5_removals(path)
    key = list(zip(ev.dir.values, ev.tj.values // 3600000))
    out = ev.copy()
    out["s5"] = [int(k in rem) for k in key]
    out["s5x"] = [int(k in excl) for k in key]
    return out


KINDS = {"s1": ((60,), s1_flags), "s2": ((60, 240, 1440), s2_flags), "s4": ((60, 240, 1440), s4_flags), "s5": ((60,), s5_flags)}

if __name__ == "__main__":
    kind, evd, src, outp = sys.argv[1:5]
    syms = sys.argv[5:]
    tfs, fn = KINDS.get(kind, ((), None))
    parts = []
    for tf in tfs:
        names = sorted(f[:-len(f"-{tf}.csv.gz")] for f in os.listdir(evd) if f.endswith(f"-{tf}.csv.gz"))
        for sym in names:
            if syms and sym not in syms:
                continue
            if kind == "s3":
                break
            e = pd.read_csv(f"{evd}/{sym}-{tf}.csv.gz")
            e = e[(e.ev == "A5") & (e.role == "real")].reset_index(drop=True)
            if len(e):
                parts.append(fn(e, tf, f"{src}/{sym}.csv.gz"))
    if kind == "s3":
        for sym in sorted(f[:-len("-60.csv.gz")] for f in os.listdir(evd) if f.endswith("-60.csv.gz")):
            if not syms or sym in syms:
                parts.append(s3_events(f"{src}/{sym}.csv.gz", sym))
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    out.to_csv(outp, index=False, compression="gzip")
    print(outp, len(out), int(out.ss_bps.notna().sum()) if len(out) else 0)
