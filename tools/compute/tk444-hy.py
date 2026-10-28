#!/usr/bin/env python3
"""TK-444 (возврат Судьи п.1): лаговая кросс-ковариация Хаяши–Ёсиды (HY) приращений лог-цены linear (X) и spot (Y) по сделкам (мс).
worker: tk444-hy.py work <СИМ> <events.csv> <outdir> [<имя spot>]   |   agg: tk444-hy.py agg <outdir> <out.csv>
Окно = [t_start, t_start+min(длит.,CAP_HY)]; те же события/контроли ±7/14/21 сут, что в tk444-lead.py. Сделки одной мс схлопнуты (последняя).
Лаг L: сумма dX(a,b]·dY(c-L,d-L] по пересекающимся интервалам, т.е. corr(dX_lin(t), dY_spot(t+L)); пик при L>0 = спот повторяет фьючерс (фьюч ведёт)."""
import sys, os, csv, bisect, importlib.util, random, math
spec = importlib.util.spec_from_file_location("lead", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk444-lead.py"))
lead = importlib.util.module_from_spec(spec); spec.loader.exec_module(lead)
CAP_HY = 60 * 60000; LAGS = (-3000, -1000, -500, -250, -100, 0, 100, 250, 500, 1000, 3000); DAY = lead.DAY

def collapse(ts, px, t0, t1):
    i = bisect.bisect_left(ts, t0); j = bisect.bisect_right(ts, t1)
    t = []; x = []
    for k in range(i, j):
        if t and t[-1] == ts[k]: x[-1] = math.log(px[k])
        else: t.append(ts[k]); x.append(math.log(px[k]))
    return t, x

def hy(tx, x, ty, y, L):
    i = j = 1; nx = len(tx); ny = len(ty); s = 0.0
    while i < nx and j < ny:
        a = tx[i-1]; b = tx[i]; c = ty[j-1] - L; d = ty[j] - L
        if b > c and d > a: s += (x[i] - x[i-1]) * (y[j] - y[j-1])
        if b < d: i += 1
        elif d < b: j += 1
        else: i += 1; j += 1
    return s

def work(sym, evf, outdir, spot=None):
    evs = []
    with open(evf, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["инструмент"] == sym and r["биржа_рынок"] == "bybit-linear":
                evs.append((int(r["D_мин"]), r["id"], 1 if r["знак"] == "+" else -1, int(r["t_start_ms"]), int(r["t_peak_ms"])))
    if not evs: return
    ms = lead.merge([(t0, t1) for _, _, _, t0, t1 in evs]); msa = [m[0] for m in ms]
    wins = []
    for D, i, s, t0, t1 in evs:
        L = min(t1 - t0, CAP_HY)
        wins.append(("ev", D, i, s, t0, t0 + L))
        for sh in lead.SHIFTS:
            a = t0 + sh * DAY; b = a + L
            k = bisect.bisect_right(msa, b) - 1
            if k >= 0 and ms[k][1] >= a: continue
            wins.append(("ctl", D, i, s, a, b))
    ivs = lead.merge([(a, b) for _, _, _, _, a, b in wins])
    lt, lp = lead.load((sym, spot or sym), "lin", ivs); st_, sp = lead.load((sym, spot or sym), "spot", ivs)
    with open(os.path.join(outdir, sym + ".csv"), "w") as o:
        o.write("инструмент,вид,D,id,знак,t0,nx,ny,vx,vy," + ",".join("c%d" % L for L in LAGS) + "\n")
        for kind, D, i, s, a, b in wins:
            tx, x = collapse(lt, lp, a, b); ty, y = collapse(st_, sp, a, b)
            if len(tx) < 20 or len(ty) < 20: continue
            vx = sum((x[k] - x[k-1]) ** 2 for k in range(1, len(x))); vy = sum((y[k] - y[k-1]) ** 2 for k in range(1, len(y)))
            o.write("%s,%s,%d,%s,%d,%d,%d,%d,%.6g,%.6g,%s\n" % (sym, kind, D, i, s, a, len(tx), len(ty), vx, vy,
                    ",".join("%.6g" % hy(tx, x, ty, y, L) for L in LAGS)))

def rho(rows, L):
    c = sum(r["c%d" % L] for r in rows); vx = sum(r["vx"] for r in rows); vy = sum(r["vy"] for r in rows)
    return c / math.sqrt(vx * vy) if vx > 0 and vy > 0 else float("nan")

def asym(rows):  # >0: спот повторяет фьючерс (фьюч ведёт)
    return sum(rho(rows, L) - rho(rows, -L) for L in (100, 250, 500, 1000, 3000)) / 5

def peak(rows): return max(LAGS, key=lambda L: rho(rows, L))

def agg(outdir, outcsv):
    import glob
    rows = []
    for f in sorted(glob.glob(outdir + "/*.csv")):
        with open(f) as fh:
            for r in csv.DictReader(fh):
                for k in list(r)[6:]: r[k] = float(r[k])
                r["кл"] = r["инструмент"] + str(int(r["t0"]) // DAY); rows.append(r)
    random.seed(444); res = {}
    for kind in ("ev", "ctl"):
        sub = [r for r in rows if r["вид"] == kind]; cl = {}
        for r in sub: cl.setdefault(r["кл"], []).append(r)
        keys = list(cl); bs = []
        for _ in range(300):
            samp = [r for c in random.choices(keys, k=len(keys)) for r in cl[c]]
            bs.append((asym(samp), peak(samp), rho(samp, 0), rho(samp, 500)))
        res[kind] = (len(sub), len(keys), asym(sub), peak(sub), rho(sub, 0), rho(sub, 500), bs)
    def ci(v): v = sorted(v); return v[int(.025 * len(v))], v[int(.975 * len(v)) - 1]
    with open(outcsv, "w", encoding="utf-8") as o:
        o.write("вид,окон,кластеров,асимметрия_HY,asym_lo,asym_hi,пик_лаг_мс,rho_0,rho_+500,rho_-500\n")
        for kind in ("ev", "ctl"):
            n, nc, a, pk, r0, r5, bs = res[kind]; lo, hi = ci([b[0] for b in bs])
            sub = [r for r in rows if r["вид"] == kind]
            o.write("%s,%d,%d,%.4f,%.4f,%.4f,%d,%.4f,%.4f,%.4f\n" % (kind, n, nc, a, lo, hi, pk, r0, r5, rho(sub, -500)))
        d = [e[0] - c[0] for e, c in zip(res["ev"][6], res["ctl"][6])]; lo, hi = ci(d)
        o.write("разн_ev-ctl,,,%.4f,%.4f,%.4f,,,,\n" % (res["ev"][2] - res["ctl"][2], lo, hi))
        o.write("# rho по лагам (все окна ev/ctl): " + " ".join("%d:%.3f/%.3f" % (L, rho([r for r in rows if r["вид"] == "ev"], L), rho([r for r in rows if r["вид"] == "ctl"], L)) for L in LAGS) + "\n")
    print(open(outcsv, encoding="utf-8").read())

if __name__ == "__main__":
    if sys.argv[1] == "work": work(*sys.argv[2:6])
    else: agg(*sys.argv[2:4])
