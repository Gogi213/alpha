#!/usr/bin/env python3
"""TK-414 слой 3: стакан Gate фьючерсов по фазам эпизода (событийный: час-файл = snapshot set + make/take, знак size: + бид, - аск).
Выборка: до N ног на форму (seed 414), Gate futures_usdt, длит. ≤ MAXH ч, все часы окна [−2 ч, конец+30 мин] ≤ MAXMB МБ (HEAD).
Фазы: base [−2ч,−30м), pre [−30м,старт), h1, h2 (половины ноги до пика+1м), post [пик+1м,+30м).
Метрики на минутных срезах: спред bps, глубина ≤25 bps с каждой стороны (контрактов, в долях медианы base той же стороны),
дисбаланс (b−a)/(b+a) на ≤25 bps, темп make/take (контр./мин, в долях медианы base). Размер в $ не нужен — всё относительное.
Использование: tk414-ob.py <legs.csv> <выход-каталог> [N=5] [процессов=3]"""
import csv, gzip, heapq, io, os, random, subprocess, sys, time, urllib.request, urllib.error
from calendar import timegm
from datetime import datetime
from multiprocessing import Pool
from statistics import median

MAXH, MAXMB, BAND = 6, 25, 25.0
URL = "https://download.gatedata.org/futures_usdt/orderbooks/%s/%s-%s.csv.gz"


def ep(s):
    return timegm(datetime.strptime(s, "%Y-%m-%dT%H:%M").timetuple())


def hours(t0, t1):
    h = t0 // 3600
    while h * 3600 < t1:
        yield h * 3600
        h += 1


def hname(h):
    return datetime.utcfromtimestamp(h).strftime("%Y%m%d%H")


def size(ins, h):
    p = subprocess.run(["curl", "-sI", "-m", "30", URL % (hname(h)[:6], ins, hname(h))], capture_output=True, text=True).stdout
    code = p.split()[1] if p.startswith("HTTP") else "0"
    n = 0
    for l in p.splitlines():
        if l.lower().startswith("content-length:"):
            n = int(l.split()[1])
    return code, n


def opener(url):
    for a in range(6):
        try:
            return urllib.request.urlopen(url, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(5 * (a + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3 * (a + 1))
    raise RuntimeError(url)


def replay(ins, h, lo, hi, rows):
    """минутные срезы за час h в [lo,hi): (минута_epoch, bid, ask, depthB, depthA, addvol, remvol)"""
    r = opener(URL % (hname(h)[:6], ins, hname(h)))
    if r is None:
        return False
    bids, asks = {}, {}
    cur_min = None
    addv = remv = 0.0

    def snap(m):
        if not bids or not asks:
            return
        bb = max((p for p, q in bids.items() if q > 0), default=None)
        ba = min((p for p, q in asks.items() if q > 0), default=None)
        if bb is None or ba is None or ba <= bb:
            return
        mid = (bb + ba) / 2
        lo_p, hi_p = mid * (1 - BAND / 1e4), mid * (1 + BAND / 1e4)
        db = sum(q for p, q in bids.items() if q > 0 and p >= lo_p)
        da = sum(q for p, q in asks.items() if q > 0 and p <= hi_p)
        rows.append((m, bb, ba, db, da, addv, remv))

    with gzip.open(io.BytesIO(r.read()), "rt") as f:
        for line in f:
            t, a, p, s, _, _ = line.split(",")
            ts = int(float(t))
            m = ts // 60 * 60
            if cur_min is None:
                cur_min = m
            if m != cur_min:
                if lo <= cur_min < hi:
                    snap(cur_min)
                cur_min = m
                addv = remv = 0.0
            p = float(p); s = float(s); q = abs(s)
            d = bids if s > 0 else asks
            if a == "set":
                d[p] = q
            elif a == "make":
                d[p] = d.get(p, 0) + q; addv += q
            else:
                d[p] = d.get(p, 0) - q; remv += q
    if lo <= cur_min < hi:
        snap(cur_min)
    return True


def work(job):
    leg, = job
    ins = leg["инструмент"]
    t0, t1 = ep(leg["начало_utc"]), ep(leg["пик_utc"]) + 60
    lo, hi = t0 - 7200, t1 + 1800
    rows = []
    for h in hours(lo, hi):
        if not replay(ins, h, lo, hi, rows):
            return leg, None
    return leg, rows


def phases(leg, rows):
    t0, t1 = ep(leg["начало_utc"]), ep(leg["пик_utc"]) + 60
    mid = (t0 + t1) // 2
    ph = {"base": (t0 - 7200, t0 - 1800), "pre": (t0 - 1800, t0), "h1": (t0, mid), "h2": (mid, t1), "post": (t1, t1 + 1800)}
    by = {k: [r for r in rows if a <= r[0] < b] for k, (a, b) in ph.items()}
    base = by["base"]
    if len(base) < 20:
        return None
    mb = median(r[3] for r in base) or 1e-9
    ma = median(r[4] for r in base) or 1e-9
    mad = median(r[5] + r[6] for r in base) or 1e-9
    out = {}
    for k, rs in by.items():
        if not rs:
            continue
        sp = [(r[2] - r[1]) / ((r[1] + r[2]) / 2) * 1e4 for r in rs]
        imb = [(r[3] - r[4]) / (r[3] + r[4]) if r[3] + r[4] > 0 else 0 for r in rs]
        out[k] = dict(n=len(rs), spread_bps=median(sp), depthB=median(r[3] for r in rs) / mb, depthA=median(r[4] for r in rs) / ma,
                      imb=median(imb), flow=median(r[5] + r[6] for r in rs) / mad,
                      rem_share=sum(r[6] for r in rs) / max(sum(r[5] + r[6] for r in rs), 1e-9))
    return out


def main():
    legs_csv, outdir = sys.argv[1:3]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    nproc = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    os.makedirs(outdir, exist_ok=True)
    random.seed(414)
    by = {}
    for r in csv.DictReader(open(legs_csv, encoding="utf-8")):
        if r["биржа"] == "gate" and r["рынок"] == "futures_usdt" and float(r["длит_мин"]) <= MAXH * 60:
            by.setdefault((r["форма"], r["знак"]), []).append(r)
    jobs = []
    for (form, sgn), ls in sorted(by.items()):
        random.shuffle(ls)
        got = 0
        for leg in ls:
            if got >= n:
                break
            t0, t1 = ep(leg["начало_utc"]), ep(leg["пик_utc"]) + 60
            ok = True
            for h in hours(t0 - 7200, t1 + 1800):
                code, nb = size(leg["инструмент"], h)
                if code != "200" or nb > MAXMB * 2**20:
                    ok = False
                    break
            if ok:
                jobs.append((leg,)); got += 1
        print(form, sgn, "кандидатов", len(ls), "взято", got, flush=True)
    with Pool(nproc) as pool, open(os.path.join(outdir, "ob-phases.csv"), "w", encoding="utf-8", newline="") as fo:
        wr = csv.writer(fo)
        wr.writerow(["инструмент", "знак", "форма", "начало_utc", "пик_utc", "ход_pct", "фаза", "минут", "спред_bps", "глубина_бид_x", "глубина_аск_x", "дисбаланс", "поток_x", "доля_снятий"])
        for leg, rows in pool.imap_unordered(work, jobs):
            if rows is None:
                print("нет файла", leg["инструмент"], leg["начало_utc"], flush=True); continue
            res = phases(leg, rows)
            if res is None:
                print("мало базы", leg["инструмент"], leg["начало_utc"], flush=True); continue
            for k, v in res.items():
                wr.writerow([leg["инструмент"], leg["знак"], leg["форма"], leg["начало_utc"], leg["пик_utc"], leg["ход_pct"], k, v["n"],
                             "%.2f" % v["spread_bps"], "%.3f" % v["depthB"], "%.3f" % v["depthA"], "%.3f" % v["imb"], "%.3f" % v["flow"], "%.3f" % v["rem_share"]])
            fo.flush()
    print("готово", flush=True)


main()
