#!/usr/bin/env python3
"""TK-461/TK-466 / P-TK459, P-TK461 §12.2: слой S -> входной csv ядра TK-463. Исходов не считает.
python tk461_s_events.py <s1|s2|s3|s4|s5> <d2b-ev каталог (s3 берёт из него только список монет)> <каталог минут csv.gz> <out.csv.gz> [SYM ...]
python tk461_s_events.py s6 <docs/findings каталог> <каталог минут csv.gz> <out.csv.gz>
События S1/S2/S4 = события A5 (S3 — свои события sm_s3: A6, a8=слом, якоря k=2/k=1) (свип k=2, 1H/4H/1D) из d2b-ev TK-461 (там уже side, t0, tt0, atr_bps, ss_bps, ot1_bps, ot1_fallback);
слой S добавляет только флаги фактора bp (читаются по данным <= момента свипа, без исходов), строка не теряется: подмножество
«с фактором»/«без фактора» ядро берёт по флагу. Флаги — тем же кодом, что принятые sm_s1/sm_s2/sm_s4 (импорт, логика не переписана).
  s1 (1H): asia/frankfurt/london/ny = час свипа по Нью-Йорку (S1.SES), 0/1.
  s2 (1H/4H/1D): pa, pb = положение в диапазоне (G2, ТФ свипа / старший), prem_a, prem_b = p>0,5 (NaN если p NaN или p==0,5).
  s5 (1H): s5 = час свипа = час первого снятия границы Азии в ту же сторону (с контрольными сдвигами G5), s5x = исключить.
  s6: 71 решённая цепочка канала (TK-454) - собственные события (tf=1 мин), сторона/уровни из сделок автора, метки sm_s6 (session_*, d1, prem).
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
    стоп = дальняя граница щели: ss_bps = (far - c[t0])/|c[t0]|*1e4 > 0. Якоря k=2 (Санчо) и k=1 (bp) -> колонка anchor. p459 = 1 - событие входит в выборку P-TK459 (там нужен контрольный бар C2; без него sm_s3 событие не считает, ядру контроль не нужен). Исход (fulfill/инвалидация) не пишется."""
    S3 = _load("sm_s3")
    KI, KB, GMIN, N, WIN, RATIO = S3.KI, S3.KB, S3.GMIN, S3.N, S3.WIN, S3.RATIO
    out = []
    for tf, tfn in S3.TFS.items():
        a = S3.bars(path, tf); ok = a.ok.values; tms = a.t.values; slots = 1440 // tf
        for k in S3.VARS:
            for dname, sgn in (("H", 1), ("L", -1)):
                if sgn == 1:
                    h, l, c = (a[x].values.astype(float) for x in "hlc")
                else:
                    h, l, c = -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
                n = len(h)
                atr = S3.atr14(h, l, c)
                atrr = atr / np.where(c != 0, np.abs(c), np.nan)
                gapb = np.zeros(n, bool)
                gapb[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (l[:-2] > h[2:])
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
                    # p459 = 1: событие попало в выборку P-TK459 (там нужен хотя бы один контрольный бар C2 - строка sm_s3.one `if np.isnan(a0) or not len(cand): continue`)
                    cand = np.arange(max(t0 - WIN * slots, 14), min(t0 + WIN * slots, n - N - 2) + 1)
                    cand = cand[(cand - t0) % slots == 0]
                    cand = cand[cand != t0]
                    cand = cand[ok[cand] & ~gapb[cand] & ~np.isnan(atrr[cand])]
                    a0 = atrr[t0]
                    if not np.isnan(a0) and len(cand):
                        cand = cand[(atrr[cand] <= a0 * RATIO) & (atrr[cand] >= a0 / RATIO)]
                        if len(cand):
                            cand = cand[np.array([okc[x + N + 1] - okc[x + 1] == N for x in cand], bool)]
                    p459 = int(not np.isnan(a0) and len(cand) > 0)
                    out.append(dict(sym=sym, tf=tf, ev="S3", dir=dname, side=-sgn, anchor=k, i=int(i), j=int(j), t0=int(t0), tt0=int(tms[t0]), a8=fl, p459=p459,
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


def s6_events(findings, src):
    """S6 (sm_s6, отбор слово в слово: 71 решённая цепочка, цель1 первой 48 - сверка, иначе стоп): события не бары-свипы, а входы автора канала.
    tf=1 (минутные бары), t0 = индекс минуты входа entry_min (tt0 = её открытие), сторона = направление сделки (long -> +1, dir=L; short -> -1, dir=H),
    ss_bps = |вход - стоп автора|/вход*1e4, atr_bps = ATR14 минутных баров на баре t0-1 (минута входа ещё не закрыта). Метки sm_s6 (session_*, d1, prem) - по закрытым барам до минуты входа.
    Исход (цель1/стоп, y) не пишется; уровни entry/stop/tp1 - справочно (entry_px, stop_px, tp1_px)."""
    import csv
    S = "-2026-10-30.csv"
    rd = lambda n: list(csv.DictReader(open(f"{findings}/{n}{S}", encoding="utf-8")))
    sys.argv = [sys.argv[0], findings]            # sancho_channel_report читает argv[1]
    ns = {}
    exec(compile(open(os.path.join(HERE, "..", "sancho_channel_report.py"), encoding="utf-8").read().split("seen = {}")[0], "rep", "exec"), ns)
    cat = ns["cat"]
    meta = {m["trade_id"]: m for m in rd("sancho-trades-meta")}
    seen = {}
    for r in rd("sancho-trades-p1-all"):
        seen.setdefault(r["trade_id"], r)
    lv = {}
    for r in rd("sancho-trades"):
        try:
            lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp1"])))
        except ValueError:
            pass
    for r in rd("sancho-trades-handtv"):
        try:
            lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp"])))
        except ValueError:
            pass
    dec = [r for r in seen.values() if cat(r) in ("первым цель1", "первым стоп") and r["trade_id"] in lv]
    assert len(dec) == 71 and sum(cat(r) == "первым цель1" for r in dec) == 48, "расхождение с TK-454 - стоп"

    def path(sym):
        for q in (f"{src}/{sym}.csv.gz", f"{src}/1000{sym}.csv.gz"):
            if os.path.exists(q):
                return q

    rows = []
    for r in dec:
        i = r["trade_id"]
        e0, st, tp = lv[i]
        rows.append(s6_row(path(meta[i]["symbol"]), meta[i]["symbol"], r["dir"] == "long", int(r["entry_min"].split("|")[0]), e0, st, tp,
                           int(i), int(meta[i]["year"]), meta[i]["session"]))
    return pd.DataFrame(rows)


_BARS = {}


def _frac(h, l, ok):
    o = []
    for i in range(1, len(h) - 1):
        if not (ok[i - 1] and ok[i] and ok[i + 1]):
            continue
        hi = h[i] > h[i - 1] and h[i] > h[i + 1]
        lo = l[i] < l[i - 1] and l[i] < l[i + 1]
        if hi != lo:
            o.append((i + 1, 1 if hi else -1))
    return o


def s6_row(pth, sym, lng, tm, e0, st, tp, tid, year, session):
    """Одна цепочка S6 -> строка события (см. s6_events). pth=None (нет минут монеты) -> atr_bps/d1/prem = NaN."""
    from sancho import bars as _b, swings, atr14
    bars = lambda q, tf: _BARS.setdefault((q, tf), _b(q, tf))      # один ряд на (монета, ТФ): цепочек одной монеты несколько
    d1 = pr = atr_bps = np.nan
    if pth:
        M = bars(pth, 1)
        k = tm // 60000 - M.index.min()
        if 14 <= k <= len(M):
            c = M.c.values.astype(float)
            a = atr14(M.h.values.astype(float), M.l.values.astype(float), c)
            atr_bps = a[k - 1] / abs(c[k - 1]) * 1e4
        D, H1 = bars(pth, 1440), bars(pth, 60)
        h, l, ok = D.h.values.astype(float), D.l.values.astype(float), D.ok.values
        b0 = D.index.min()
        fr = [(c_, t) for c_, t in _frac(h, l, ok) if (b0 + c_ + 1) * 86400000 <= tm]
        if fr:
            d1 = int(fr[-1][1] == (1 if lng else -1))
        h, l, ok = H1.h.values.astype(float), H1.l.values.astype(float), H1.ok.values
        lim = tm // 3600000 - H1.index.min()
        sh, sl = swings(h, ok, 2), swings(-l, ok, 2)
        sh = sh[sh + 2 < lim]
        sl = sl[sl + 2 < lim]
        if len(sh) and len(sl):
            a_, b_ = sh[-1], sl[-1]
            z = sl[sl < a_] if a_ > b_ else sh[sh < b_]
            if len(z):
                hi, lo = (h[a_], l[z[-1]]) if a_ > b_ else (h[z[-1]], l[b_])
                if hi > lo:
                    pr = (e0 - lo) / (hi - lo)
    ss = abs(e0 - st) / abs(e0) * 1e4
    return dict(sym=sym, tf=1, ev="S6", dir="L" if lng else "H", side=1 if lng else -1, id=tid, year=year,
                t0=tm // 60000, tt0=tm, atr_bps=atr_bps, ss_bps=ss, ot1_bps=ss, ot1_fallback=0,
                **{"session_" + k: int(session == v) for k, v in (("asia", "Азия"), ("frankfurt", "Франкфурт"), ("london", "Лондон"), ("ny", "Нью-Йорк"))},
                d1=d1, prem=np.nan if np.isnan(pr) else int((pr < .5) if lng else (pr > .5)), entry_px=e0, stop_px=st, tp1_px=tp)


KINDS = {"s1": ((60,), s1_flags), "s2": ((60, 240, 1440), s2_flags), "s4": ((60, 240, 1440), s4_flags), "s5": ((60,), s5_flags)}

if __name__ == "__main__":
    kind, evd, src, outp = sys.argv[1:5]
    syms = sys.argv[5:]
    if kind == "s6":
        out = s6_events(evd, src)
        out.to_csv(outp, index=False, compression="gzip")
        print(outp, len(out), int(out.ss_bps.notna().sum()))
        sys.exit(0)
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
