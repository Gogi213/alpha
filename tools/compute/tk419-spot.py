#!/usr/bin/env python3
"""TK-419 вар. 2 (описательно, вне вердикта): события linear (A) монет, у которых есть spot ob200; признаки ob_dz/ob_sp по книге SPOT в полосах 25/50/100 бп и «всё ob200»;
правила O1 (ob_dz<=1/3), O3 (ob_sp>=3), O4 (B и O1) как в P-TK414-ob200; марк-аут M_h — из markout-A.csv. Срез — конец минуты floor(t)-1, база 360 мин до начала окна B (минB), последний срез не старше 5 мин.
Использование:
  tk419-spot.py срезы <корень spot ob> <выход-каталог> <markout-A.csv> <процессов>  → <выход>/<SYM>/<сутки>.csv: минута,bb,ba,dB25,dA25,dB50,dA50,dB100,dA100,dBall,dAll
  tk419-spot.py сводка <каталог срезов> <markout-A.csv> <obfeat-events.csv> <выход.csv>"""
import csv, glob, json, os, random, sys, zipfile
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from multiprocessing import Pool

BANDS = (25.0, 50.0, 100.0, None)
HS = (5, 15, 60, 240)


def lines_of(fh):
    tail = b''
    while True:
        buf = fh.read(1 << 24)
        if not buf:
            break
        buf = tail + buf
        parts = buf.split(b'\n')
        tail = parts.pop()
        yield from parts
    if tail.strip():
        yield tail


def one(job):
    zp, out = job
    if os.path.exists(out):
        return out, 0, "есть"
    rows, bids, asks, cur, nerr, prev = [], {}, {}, None, 0, 0.0

    def snap(m):
        if not bids or not asks:
            return
        bb, ba = max(bids), min(asks)
        if ba <= bb:
            return
        mid = (bb + ba) / 2
        r = [m, bb, ba]
        for B in BANDS:
            if B is None:
                r += [sum(bids.values()), sum(asks.values())]
            else:
                lo, hi = mid * (1 - B / 1e4), mid * (1 + B / 1e4)
                r += [sum(q for p, q in bids.items() if p >= lo), sum(q for p, q in asks.items() if p <= hi)]
        rows.append(r)

    zf = zipfile.ZipFile(zp)
    for raw in lines_of(zf.open(zf.namelist()[0])):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
            te = float(d.get('cts') or d['ts'])
            typ, dd = d['type'], d['data']
        except Exception:
            nerr += 1
            continue
        te = max(te, prev)
        prev = te
        m = int(te // 60000) * 60
        if cur is None:
            cur = m
        if m != cur:
            snap(cur)
            cur = m
        if typ == 'snapshot':
            bids = {float(p): float(s) for p, s in dd.get('b', [])}
            asks = {float(p): float(s) for p, s in dd.get('a', [])}
        else:
            for p, s in dd.get('b', ()):
                if float(s) == 0.0:
                    bids.pop(float(p), None)
                else:
                    bids[float(p)] = float(s)
            for p, s in dd.get('a', ()):
                if float(s) == 0.0:
                    asks.pop(float(p), None)
                else:
                    asks[float(p)] = float(s)
    if cur is not None:
        snap(cur)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    os.replace(out + ".tmp", out)
    return out, len(rows), "ошибок разбора %d" % nerr


def slices(root, outd, mo, nproc):
    syms = {r["инструмент"] for r in csv.DictReader(open(mo, newline="", encoding="utf-8"))}
    jobs = []
    for zp in sorted(glob.glob(os.path.join(root, "*", "*_ob200.data.zip"))):
        day, sym = os.path.basename(zp).split("_")[:2]
        if sym in syms:
            jobs.append((zp, os.path.join(outd, sym, day + ".csv")))
    print("файлов", len(jobs), flush=True)
    with Pool(nproc) as p:
        for i, x in enumerate(p.imap_unordered(one, jobs), 1):
            print(i, *x, flush=True)
    print("готово", flush=True)


def med(x):
    y = sorted(x)
    return y[len(y) // 2]


_c = {}


def minutes(d, sym):
    if sym not in _c:
        a = []
        p = os.path.join(d, sym)
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                for line in open(os.path.join(p, fn), newline=""):
                    a.append(tuple(float(v) for v in line.split(",")))
        a.sort()
        _c[sym] = ([int(x[0]) for x in a], a)
    return _c[sym]


def feat(d, sym, t, mins, s, k):
    ks, a = minutes(d, sym)
    te = int(datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) // 60 * 60 - 60
    j = bisect_right(ks, te) - 1
    if j < 0 or te - ks[j] > 300:
        return None
    base = a[bisect_left(ks, te - mins * 60 - 360 * 60):bisect_left(ks, te - mins * 60)]
    if len(base) < 60:
        return None
    x = a[j]
    ib, ia = 3 + 2 * k, 4 + 2 * k
    opp = x[ia] if s > 0 else x[ib]
    mo = med([(y[ia] if s > 0 else y[ib]) for y in base])
    msp = med([(y[2] - y[1]) / ((y[1] + y[2]) / 2) for y in base])
    if mo <= 0 or msp <= 0:
        return None
    return opp / mo, ((x[2] - x[1]) / ((x[1] + x[2]) / 2)) / msp


def boot(v, cl, n=2000):
    """95 % интервал разности медиан (принятые − непринятые), кластерный бутстрап по инструменто-суткам; без поправки (описательно)"""
    keys = sorted(set(cl))
    idx = {}
    for i, c in enumerate(cl):
        idx.setdefault(c, []).append(i)
    rnd = random.Random(419)
    out = []
    for _ in range(n):
        pick = [i for c in (rnd.choice(keys) for _ in keys) for i in idx[c]]
        a = [v[i][0] for i in pick if v[i][1]]
        b = [v[i][0] for i in pick if not v[i][1]]
        if len(a) >= 3 and len(b) >= 3:
            out.append(med(a) - med(b))
    out.sort()
    return (out[int(len(out) * .025)], out[int(len(out) * .975)]) if len(out) > 100 else (None, None)


def summary(d, mo, obf, outc):
    mb = {}
    for r in csv.DictReader(open(obf, newline="", encoding="utf-8")):
        mb[(r["инструмент"], r["t_событие"], r["знак"])] = int(float(r["минB"] or 0))
    ev = [r for r in csv.DictReader(open(mo, newline="", encoding="utf-8")) if os.path.isdir(os.path.join(d, r["инструмент"]))]
    rows = []
    for k, nm in enumerate(("25", "50", "100", "всё")):
        ft = {id(r): feat(d, r["инструмент"], r["t_событие"], mb.get((r["инструмент"], r["t_событие"], r["знак"]), 0), int(r["знак"]), k) for r in ev}
        have = [r for r in ev if ft[id(r)]]
        for rule in ("O1", "O3", "O4"):
            def acc(r):
                dz, sp = ft[id(r)]
                b = float(r["surgeB"] or 0) >= 3 and float(r["imbB"] or 0) >= 0.1
                return dz <= 1 / 3 if rule == "O1" else (sp >= 3 if rule == "O3" else (b and dz <= 1 / 3))
            for h in HS:
                sel = [r for r in have if r["M%d" % h] != ""]
                v = [(float(r["M%d" % h]), acc(r)) for r in sel]
                cl = [(r["инструмент"], r["t_событие"][:10]) for r in sel]
                a = [x for x, f in v if f]
                b = [x for x, f in v if not f]
                ni = len({c for (x, f), c in zip(v, cl) if f})
                if len(a) < 5 or len(b) < 5:
                    rows.append((nm, rule, h, len(have), len(a), len(b), ni, "", "", "", "", ""))
                    continue
                lo, hi = boot(v, cl)
                rows.append((nm, rule, h, len(have), len(a), len(b), ni, "%.1f" % med(a), "%.1f" % med(b), "%.1f" % (med(a) - med(b)),
                             "" if lo is None else "%.1f" % lo, "" if hi is None else "%.1f" % hi))
    with open(outc, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["полоса_бп", "правило", "h_мин", "событий_с_признаком", "принятые_n", "непринятые_n", "принятые_кластеров", "мед_M_принятые", "мед_M_непринятые", "разность", "ci_lo", "ci_hi"])
        w.writerows(rows)
    print("строк", len(rows), "событий", len(ev), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "срезы":
        slices(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
    else:
        summary(*sys.argv[2:6])
