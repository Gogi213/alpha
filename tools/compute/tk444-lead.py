#!/usr/bin/env python3
"""TK-444: спот или фьючерс первым проходит k bps в окне события v11 (по сделкам Bybit, мс) против контролей.
worker: tk444-lead.py work <СИМ> <events.csv> <outdir>   |   agg: tk444-lead.py agg <outdir> <месяц> <out.csv> <out.md>
Окно = [t_start, t_start+min(длит,CAP) мин]; P0 = последняя сделка рынка до t_start (не старше 300 с);
первый проход k bps по ходу знака события; контроли = то же окно со сдвигом ±7/14/21 сут, без пересечения с событиями инструмента."""
import sys, gzip, glob, bisect, csv, os, random, statistics
from array import array
ROOT = "/data/tk417"; CAP = 240; KS = (10, 25, 50, 100, 200); SHIFTS = (7, 14, 21, -7, -14, -21); PRE = 300_000
DAY = 86_400_000

def load(sym, mkt, ivs):
    ts = array("q"); px = array("d")
    rows = []
    files = sorted(glob.glob("%s/bybit-linear/%s/*.csv.gz" % (ROOT, sym))) if mkt == "lin" else glob.glob("%s/bybit-spot/%s/*.csv.gz" % (ROOT, sym))
    starts = [a for a, b in ivs]; ends = [b for a, b in ivs]
    for f in files:
        with gzip.open(f, "rt") as fh:
            next(fh)
            for ln in fh:
                p = ln.split(",", 5)
                t = int(round(float(p[0]) * 1000)) if mkt == "lin" else int(p[1])
                i = bisect.bisect_right(starts, t) - 1
                if i >= 0 and t <= ends[i]:
                    rows.append((t, float(p[4]) if mkt == "lin" else float(p[2])))
    rows.sort()
    for t, p in rows:
        ts.append(t); px.append(p)
    return ts, px

def merge(ivs):
    ivs = sorted(ivs); out = []
    for a, b in ivs:
        if out and a <= out[-1][1]: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out

def fp(ts, px, t0, t1, s):
    i = bisect.bisect_right(ts, t0) - 1
    if i < 0 or t0 - ts[i] > PRE: return None
    p0 = px[i]; res = {}
    j = i + 1; n = len(ts)
    while j < n and ts[j] <= t1 and len(res) < len(KS):
        d = s * (px[j] / p0 - 1) * 1e4
        for k in KS:
            if k not in res and d >= k: res[k] = ts[j]
        j += 1
    return res

def work(sym, evf, outdir):
    evs = []
    with open(evf, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["инструмент"] == sym and r["биржа_рынок"] == "bybit-linear":
                evs.append((int(r["D_мин"]), r["id"], 1 if r["знак"] == "+" else -1, int(r["t_start_ms"]), int(r["t_peak_ms"])))
    if not evs: return
    allw = [(t0, t1) for _, _, _, t0, t1 in evs]; ms = merge(allw); msa = [x[0] for x in ms]
    wins = []
    for D, i, s, t0, t1 in evs:
        L = min(t1 - t0, CAP * 60000)
        wins.append(("ev", D, i, s, t0, t0 + L))
        for sh in SHIFTS:
            a = t0 + sh * DAY; b = a + L
            k = bisect.bisect_right(msa, b) - 1
            if k >= 0 and ms[k][1] >= a: continue
            wins.append(("ctl", D, i, s, a, b))
    ivs = merge([(a - PRE, b) for _, _, _, _, a, b in wins])
    lt, lp = load(sym, "lin", ivs); st_, sp = load(sym, "spot", ivs)
    with open(os.path.join(outdir, sym + ".csv"), "w") as o:
        o.write("инструмент,вид,D,id,знак,t0," + ",".join("tl%d,ts%d" % (k, k) for k in KS) + "\n")
        for kind, D, i, s, a, b in wins:
            rl = fp(lt, lp, a, b, s); rs = fp(st_, sp, a, b, s)
            if rl is None or rs is None: continue
            o.write("%s,%s,%d,%s,%d,%d," % (sym, kind, D, i, s, a) + ",".join("%s,%s" % (rl.get(k, ""), rs.get(k, "")) for k in KS) + "\n")

def wilson(x, n):
    if n == 0: return (float("nan"),) * 3
    z = 1.96; p = x / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** .5 / d
    return p, c - h, c + h

def agg(outdir, month, outcsv, outmd):
    rows = []
    for f in sorted(glob.glob(outdir + "/*.csv")):
        with open(f) as fh: rows += list(csv.DictReader(fh))
    random.seed(444)
    summ = []
    def cell(sub, k):
        d = []
        for r in sub:
            a, b = r["tl%d" % k], r["ts%d" % k]
            if a and b: d.append((int(b) - int(a), r["инструмент"] + str(int(r["t0"]) // DAY)))
        return d
    def stat(d):
        lead = [x for x, c in d if x != 0]
        n = len(lead); x = sum(1 for v in lead if v < 0)
        p, lo, hi = wilson(x, n)
        cl = {}
        for v, c in d: cl.setdefault(c, []).append(v)
        keys = list(cl); bs = []
        for _ in range(300):
            xs = n_ = 0
            for c in random.choices(keys, k=len(keys)):
                for v in cl[c]:
                    if v != 0: n_ += 1; xs += v < 0
            if n_: bs.append(xs / n_)
        bs.sort()
        cb = (bs[int(.025 * len(bs))], bs[int(.975 * len(bs)) - 1]) if bs else (float("nan"),) * 2
        return len(d), n, len(keys), p, lo, hi, cb, statistics.median([v for v, c in d]) if d else float("nan")
    out = []
    for kind in ("ev", "ctl"):
        for sg in (None, 1, -1):
            for k in KS:
                sub = [r for r in rows if r["вид"] == kind and (sg is None or int(r["знак"]) == sg)]
                n, nl, nc, p, lo, hi, cb, med = stat(cell(sub, k))
                out.append((kind, "все" if sg is None else ("+" if sg == 1 else "-"), k, n, nl, nc, p, lo, hi, cb[0], cb[1], med))
    with open(outcsv, "w", encoding="utf-8") as o:
        o.write("вид,знак,k_bps,n_пар,n_без_ничьих,кластеров_инстр-сут,доля_спот_первым,wilson_lo,wilson_hi,boot_lo,boot_hi,медиана_лаг_мс(спот-фьюч)\n")
        for r in out: o.write(",".join("%.4g" % v if isinstance(v, float) else str(v) for v in r) + "\n")
    print(open(outcsv, encoding="utf-8").read())

if __name__ == "__main__":
    if sys.argv[1] == "work": work(*sys.argv[2:5])
    else: agg(*sys.argv[2:6])
