#!/usr/bin/env python3
"""TK-433 v10: признаки конца (К1 DP/BIC, К3, З1–З3) и старта (DV24, natr ТФ 15/30/60/1сут, консолидация) по окнам событий и контролей.
Свечные признаки; К2, К4–К8 (OI, сделки) — отдельным шагом. Использование:
  tk433-end.py <legs-th{T}.csv> <T> <корень свечей> <aug-корень|-> <выход-каталог> [месяц]
Окна и контроли — cmd_windows из tk433-features.py (v9b: ±30 сут, тот же час, md5 seed 433) на ногах варианта T (θ240 не подмешивается).
Протокол: docs/research/P-TK433-v10-end-start-2026-10-28.md §2, §4, §5."""
import csv, importlib.util, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("f433", os.path.join(HERE, "tk433-features.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
MIN = 60000
KMAX, NPTS, WIN = 6, 200, 60      # k ≤ 6 (протокол), сетка ≤ 200 точек (технически: DP O(k·n²)), окна до/после 60 мин (Судья)
CAP = 1440                        # поиск пробоя линии — не дальше суток после пика (цензура фиксируется)


def load_ohlcq(roots, sub, sym):
    import gzip
    d = {}
    for r in roots:
        fn = "%s/%s/%s.csv.gz" % (r, sub, sym) if r else None
        if fn and os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                f.readline()
                for line in f:
                    p = line.split(",", 7)
                    c = float(p[4])
                    if c > 0:
                        d[int(p[0]) // MIN] = (c, float(p[6] or 0), float(p[2]), float(p[3]))
    return d


def seg_cost(x, y):
    n = len(x)
    cx, cy = np.concatenate([[0], np.cumsum(x)]), np.concatenate([[0], np.cumsum(y)])
    cxx, cxy, cyy = (np.concatenate([[0], np.cumsum(v)]) for v in (x * x, x * y, y * y))
    C = np.full((n, n), np.inf)
    for i in range(n):
        j = np.arange(i + 2, n)           # сегмент ≥ 3 точек: i..j
        m = (j - i + 1).astype(float)
        sx, sy = cx[j + 1] - cx[i], cy[j + 1] - cy[i]
        sxx, sxy, syy = cxx[j + 1] - cxx[i], cxy[j + 1] - cxy[i], cyy[j + 1] - cyy[i]
        vx, vxy = sxx - sx * sx / m, sxy - sx * sy / m
        sse = syy - sy * sy / m - np.where(vx > 0, vxy * vxy / np.where(vx > 0, vx, 1), 0)
        C[i, j] = np.maximum(sse, 1e-18)
    return C


def k1(t, y):
    """Кусочно-линейная (разрывная) аппроксимация y(t), k∈1..KMAX по BIC (ничья — меньшее k). Вернуть k и сегменты (i, j, наклон, свободный член)."""
    n = len(t)
    if n < 6:
        return None
    C = seg_cost(t, y)
    k_max = min(KMAX, n // 3)
    D = np.full((k_max + 1, n), np.inf)
    P = np.zeros((k_max + 1, n), dtype=int)
    D[1] = C[0]
    for k in range(2, k_max + 1):
        for j in range(3 * k - 1, n):      # D[k][j] = min_i D[k-1][i-1] + C[i][j], i ≥ 3(k-1)
            i = np.arange(3 * (k - 1), j - 1)
            v = D[k - 1][i - 1] + C[i, j]
            b = int(np.argmin(v))
            D[k][j], P[k][j] = v[b], int(i[b])
    best, bk = None, None
    for k in range(1, k_max + 1):
        sse = D[k][n - 1]
        if not np.isfinite(sse):
            continue
        bic = n * math.log(max(sse, 1e-18) / n) + (3 * k - 1) * math.log(n)
        if best is None or bic < best - 1e-9:
            best, bk = bic, k
    if bk is None:
        return None
    segs, j = [], n - 1
    for k in range(bk, 0, -1):
        i = 0 if k == 1 else int(P[k][j])
        segs.append((i, j))
        j = i - 1
    segs.reverse()
    out = []
    for i, j in segs:
        a, b = np.polyfit(t[i: j + 1], y[i: j + 1], 1)
        out.append((i, j, float(a), float(b)))
    return bk, out


def atr_pct(d, start, tf, n=14):
    """ATR(n)/close×100 по ТФ-свечам (из 1 м): n последних полных баров до start (SMA TR); бары выровнены по эпохе."""
    e = start - start % tf
    bars = []
    for k in range(n + 1):
        b0 = e - (k + 1) * tf
        ms = [d[m] for m in range(b0, b0 + tf) if m in d]
        if not ms:
            return None
        bars.append((ms[0][0], max(x[2] for x in ms), min(x[3] for x in ms), ms[-1][0]))
    bars.reverse()
    tr = [max(h - l, abs(h - bars[i][3]), abs(l - bars[i][3])) for i, (_, h, l, _) in enumerate(bars[1:], 0)]
    return sum(tr) / len(tr) / bars[-1][3] * 100


def window_row(d, dsp, a, e0, sg, norm, rid):
    """a — старт, e0 — пик события (контроль: экстремум окна своего знака)."""
    pts = [(m, d[m][0]) for m in range(a, e0 + 1) if m in d]
    r = dict(id=rid)
    e = max(pts, key=lambda p: p[1] * sg)[0] if rid.startswith("C-") and pts else e0
    r["peak_min"] = e
    r["dur_min"] = e - a
    pts = [(m, c) for m, c in pts if m <= e]
    res = None
    if len(pts) >= 6:
        step = max(1, math.ceil(len(pts) / NPTS))
        sub = pts[::step]
        if sub[-1][0] != pts[-1][0]:
            sub.append(pts[-1])
        t = np.array([p[0] - a for p in sub], float)
        y = np.log(np.array([p[1] for p in sub], float))
        res = k1(t, y)
    if res:
        bk, segs = res
        sl = [s[2] * sg for s in segs]
        r["k1_k"] = bk
        r["k1_mono"] = int(bk >= 2 and all(sl[i] < sl[i + 1] for i in range(bk - 1)))
        r["k1_last_steepest"] = int(bk >= 2 and sl[-1] == max(sl))
        r["k1_last_slope_bps_min"] = sl[-1] * 1e4
        aa, bb = segs[-1][2], segs[-1][3]
        above = below = tot = 0
        for m in range(e - 60, e + 61):
            if m in d:
                line = aa * (m - a) + bb
                v = math.log(d[m][0])
                tot += 1
                above += v > line
                below += v < line
        r["k3_saw"] = min(above, below) / tot if tot else None
        brk = None
        for m in range(e + 1, e + 1 + CAP):
            if m in d and (math.log(d[m][0]) - (aa * (m - a) + bb)) * sg < 0:
                brk = m - e
                break
        r["brk_min"] = brk
        r["brk_cens"] = int(brk is None)
    # З1–З3: до = [e−60, e], после = (e, e+60]
    for nm, (x, y) in (("bef", (e - WIN, e)), ("aft", (e + 1, e + WIN))):
        s = sum(d[m][1] for m in range(x, y + 1) if m in d)
        r["m_" + nm] = s / (norm * (y - x + 1)) if norm else None
        r["v_" + nm] = F.mad_demeaned(d, x, y)
        if dsp:
            bs = [abs(d[m][0] / dsp[m][0] - 1) for m in range(x, y + 1) if m in d and m in dsp]
            r["bas_" + nm] = F.med(bs) if bs else None
    # старт
    r["dv24"] = sum(d[m][1] for m in range(a - 1440, a) if m in d)
    for nm, tf in (("natr15", 15), ("natr30", 30), ("natr60", 60), ("natr1d", 1440)):
        r[nm] = atr_pct(d, a, tf)
    cl = [d[m][0] for m in range(a - 60, a) if m in d]
    v7 = F.mad_demeaned(d, a - 7 * 1440, a - 1)
    r["cons_range"] = (max(cl) - min(cl)) / cl[-1] if len(cl) > 5 else None
    r["cons_ratio"] = r["cons_range"] / v7 if r["cons_range"] is not None and v7 else None
    return r


def main():
    legs, T, root, aug, outd = sys.argv[1:6]
    ym = sys.argv[6] if len(sys.argv) > 6 else "2026-09"
    os.makedirs(outd, exist_ok=True)
    empty = os.path.join(outd, "empty-legs.csv")
    with open(legs, encoding="utf-8") as f, open(empty, "w", encoding="utf-8") as g:
        g.write(f.readline())
    F.cmd_windows([legs, empty, root, aug, outd, ym])    # ноги варианта T идут как «θ60» файла windows.csv
    W = list(csv.DictReader(open(os.path.join(outd, "windows.csv"), encoding="utf-8")))
    roots = (aug if aug != "-" else None, root)
    busy = {}
    for r in csv.DictReader(open(legs, encoding="utf-8")):
        busy.setdefault((r["биржа"], r["рынок"], r["инструмент"]), []).append((int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN))
    by = {}
    for w in W:
        by.setdefault((w["биржа"], w["рынок"], w["инструмент"]), []).append(w)
    rows = []
    for (ex, mk, sym), L in sorted(by.items()):
        d = load_ohlcq(roots, "%s-%s" % (ex, mk), sym)
        dsp = load_ohlcq(roots, "%s-%s" % (ex, F.PAIR[mk]), sym) if mk in F.PAIR else {}
        bz = busy.get((ex, mk, sym), [])
        for w in L:
            a, e0 = int(w["t_start"]) // MIN, int(w["t_peak"]) // MIN
            q = [d[m][1] for m in range(a - 7 * 1440, a) if m in d and not any(i0 <= m <= i1 for i0, i1 in bz)]
            norm = F.med(q) if len(q) > 1440 else None
            r = window_row(d, dsp, a, e0, int(w["знак"]), norm, w["id"])
            r.update(event_id=w["event_id"], роль=w["роль"], T=T, биржа=ex, рынок=mk, инструмент=sym, знак=w["знак"], start_min=a)
            rows.append(r)
    head = ("id", "event_id", "роль", "T", "биржа", "рынок", "инструмент", "знак")
    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in head, head.index(k) if k in head else 0, k))
    with open(os.path.join(outd, "end-start-th%s.csv" % T), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    print("окон", len(rows), "событий", sum(r["роль"] == "event" for r in rows))


if __name__ == "__main__":
    main()
