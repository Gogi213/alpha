#!/usr/bin/env python3
"""TK-420 вар. 3, признак D4 «мс-срез книги» (протокол P-TK420-pump-dynamics-end-month2-2026-10-27.md v2, утверждён Судьёй; считать A, Bybit linear).
Событие мс: онлайн-зигзаг θs=5 % по закрытиям 1 м (tk414-detector.online, те же despike/свечи) даёт уровень L = c[экстремум]·(1+s·0,05);
τ_ms = время первой сделки минуты события [t_i, t_i+60 с), пересёкшей L (s=+1: цена ≥ L; s=−1: ≤ L); если ни одна не пересекла (despike
сдвинул закрытие) — последняя сделка минуты, флаг fb=1. Сделок в минуте нет — событие без D4.
Книга: ob200 Bybit (snapshot/delta по cts, как tk414-obmin.py), глубина встречной стороны (ask при s=+1, bid при s=−1) в полосе 25 бп от середины.
d(T) — состояние после всех сообщений с cts ≤ T. r(t) = d(t−10 с)/d(t−60 с). D4 = r(τ_ms). База: r в точках t = τ_ms − минB·60 с − k·15 мин, k=1…24
(как база D1/D3: окно [τ−минB−360 мин, τ−минB)). Выбранные: D4 ≤ нижнего тертиля (33,3-й процентиль) базы своей монеты; значений базы ≥ MINBASE.
Вход: d3-A.csv (события, минB). Выход: <выход>/<SYM>.d4.csv: ключ события + tau_ms, fb, d4, thr4, nbase, sel_D4, age_ms (возраст последнего сообщения книги перед τ−10 с);
<выход>/<SYM>.stat.txt: время и объёмы (калибровка).
Использование: tk420-var3-d4.py <d3-A.csv> <корень свечей> <корень сделок tk420/tr/bybit/bybit-linear> <корень ob200 .../bybit-linear> <выход> <процессов> [SYM,SYM…]"""
import csv, glob, gzip, importlib.util, json, os, sys, time, zipfile
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("tk413", os.path.join(HERE, "tk413-episodes.py")); T13 = importlib.util.module_from_spec(sp); sp.loader.exec_module(T13)
sp = importlib.util.spec_from_file_location("tk414d", os.path.join(HERE, "tk414-detector.py")); T14 = importlib.util.module_from_spec(sp); sp.loader.exec_module(T14)
BAND, TH, MINBASE = 25.0, 0.05, 12
DAY = 86400000


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def lines_of(fh):
    tail = b''
    while True:
        buf = fh.read(1 << 24)
        if not buf:
            break
        parts = (tail + buf).split(b'\n')
        tail = parts.pop()
        yield from parts
    if tail.strip():
        yield tail


def event_ms(candles, trroot, sym, evs):
    """evs: список строк событий -> (tau_ms, fb) по ключу (t_событие, знак)."""
    t, c, q, n, w = T13.load(candles, "bybit", "linear", sym)
    t, c, q, n, w, _ = T13.despike(t, c, q, n, w, )
    det = {}
    for (i, s, ext) in T14.online(c, TH, False):
        det[(T13.iso(t[i]), s)] = (t[i], c[ext] * (1 + s * TH))
    res, cache = {}, {}
    for r in evs:
        key = (r["t_событие"], int(r["знак"]))
        if key not in det:
            continue
        t0, lvl = det[key]
        d = day_of(t0)
        if d not in cache:
            fn = os.path.join(trroot, sym, "%s%s.csv.gz" % (sym, d))
            tr = []
            if os.path.exists(fn):
                with gzip.open(fn, "rt") as f:
                    next(f)
                    for line in f:
                        p = line.split(",")
                        tr.append((int(float(p[0]) * 1000), float(p[4])))
                tr.sort()
            cache[d] = ([x[0] for x in tr], [x[1] for x in tr])
        ts, px = cache[d]
        a, b = bisect_left(ts, t0), bisect_left(ts, t0 + 60000)
        if a == b:
            continue
        s = key[1]
        for j in range(a, b):
            if (s == 1 and px[j] >= lvl) or (s == -1 and px[j] <= lvl):
                res[key] = (ts[j], 0)
                break
        else:
            res[key] = (ts[b - 1], 1)
    return res


def book_pass(zp, times):
    """times: отсортированные T (мс) внутри суток файла. -> {T: (dB, dA, age_ms)}."""
    out = {}
    bids, asks = {}, {}
    zf = zipfile.ZipFile(zp)
    fh = zf.open(zf.namelist()[0])
    k, prev, last_te, nmsg = 0, 0.0, None, 0

    def rec(T):
        if not bids or not asks:
            return
        bb, ba = max(bids), min(asks)
        if ba <= bb:
            return
        mid = (bb + ba) / 2
        lo, hi = mid * (1 - BAND / 1e4), mid * (1 + BAND / 1e4)
        out[T] = (sum(q for p, q in bids.items() if p >= lo), sum(q for p, q in asks.items() if p <= hi), int(T - last_te))

    for raw in lines_of(fh):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw); te = float(d.get('cts') or d['ts']); typ = d['type']; dd = d['data']
        except Exception:
            continue
        if te < prev:
            te = prev
        prev = te
        while k < len(times) and times[k] < te:
            if last_te is not None:
                rec(times[k])
            k += 1
        if k >= len(times):
            break
        if typ == 'snapshot':
            bids = {float(p): float(s) for p, s in dd.get('b', [])}
            asks = {float(p): float(s) for p, s in dd.get('a', [])}
        else:
            for p, s in dd.get('b', ()):
                if float(s) == 0.0: bids.pop(float(p), None)
                else: bids[float(p)] = float(s)
            for p, s in dd.get('a', ()):
                if float(s) == 0.0: asks.pop(float(p), None)
                else: asks[float(p)] = float(s)
        last_te = te
        nmsg += 1
    return out, nmsg


def work(arg):
    sym, evs, candles, trroot, obroot, outd = arg
    t_start = time.time()
    em = event_ms(candles, trroot, sym, evs)
    keep = []
    for r in evs:
        key = (r["t_событие"], int(r["знак"]))
        if key in em:
            keep.append((r, em[key]))
    need = set()
    for r, (tau, fb) in keep:
        base = tau - int(r["минB"]) * 60000
        pts = [tau] + [base - k * 900000 for k in range(1, 25)]
        for p in pts:
            need.add(p - 10000); need.add(p - 60000)
    times = sorted(need)
    by_day = {}
    for T in times:
        by_day.setdefault(day_of(T), []).append(T)
    samp, nfile, nmsg = {}, 0, 0
    for d, ts in by_day.items():
        zp = os.path.join(obroot, sym, "%s_%s_ob200.data.zip" % (d, sym))
        if not os.path.exists(zp):
            zp = os.path.join(obroot, "%s_%s_ob200.data.zip" % (d, sym))
        if not os.path.exists(zp):
            continue
        o, n = book_pass(zp, ts)
        samp.update(o); nfile += 1; nmsg += n
    rows = []
    for r, (tau, fb) in keep:
        s = int(r["знак"])
        ix = 1 if s == 1 else 0

        def ratio(p):
            a, b = samp.get(p - 10000), samp.get(p - 60000)
            if a is None or b is None or b[ix] <= 0:
                return None
            return a[ix] / b[ix]
        d4 = ratio(tau)
        base = tau - int(r["минB"]) * 60000
        bv = [x for x in (ratio(base - k * 900000) for k in range(1, 25)) if x is not None]
        thr, sel = "", ""
        if d4 is not None and len(bv) >= MINBASE:
            vs = sorted(bv); q = (len(vs) - 1) / 3; i = int(q)
            th = vs[i] + (vs[min(i + 1, len(vs) - 1)] - vs[i]) * (q - i)
            thr = "%.6f" % th; sel = "1" if d4 <= th else "0"
        a = samp.get(tau - 10000)
        rows.append([sym, r["t_событие"], r["знак"], tau, fb, "" if d4 is None else "%.6f" % d4, thr, len(bv), sel, "" if a is None else a[2]])
    os.makedirs(outd, exist_ok=True)
    with open(os.path.join(outd, sym + ".d4.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["инструмент", "t_событие", "знак", "tau_ms", "fb", "d4", "thr4", "nbase", "sel_D4", "age_ms"]); w.writerows(rows)
    el = time.time() - t_start
    st = "%s: событий %d, с мс %d, D4 есть %d, выбрано %d, файлов книги %d, сообщений %d, образцов %d, сек %.0f" % (
        sym, len(evs), len(keep), sum(1 for x in rows if x[5] != ""), sum(1 for x in rows if x[8] == "1"), nfile, nmsg, len(samp), el)
    open(os.path.join(outd, sym + ".stat.txt"), "w").write(st + "\n")
    return st


def main():
    src, candles, trroot, obroot, outd, nproc = sys.argv[1:7]
    only = set(sys.argv[7].split(",")) if len(sys.argv) > 7 else None
    by = {}
    for r in csv.DictReader(open(src, newline="", encoding="utf-8")):
        if only and r["инструмент"] not in only:
            continue
        by.setdefault(r["инструмент"], []).append(r)
    jobs = [(k, v, candles, trroot, obroot, outd) for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    with Pool(int(nproc)) as p:
        for st in p.imap_unordered(work, jobs):
            print(st, flush=True)
    print("готово", flush=True)


if __name__ == "__main__":
    main()
