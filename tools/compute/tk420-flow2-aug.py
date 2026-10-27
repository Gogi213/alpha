#!/usr/bin/env python3
"""TK-414 возврат Судьи 27.10 02:39 (п.2): окна «после дна паузы» B и «последние N мин» N15/N30 поверх tk414-flow.py.
Протокол: docs/research/P-TK414-flow-window-2026-10-27.md. Признаки сделок мс (направленный поток, всплеск) на онлайн-событиях детектора.
Протокол (заморожен коммитом до счёта): docs/research/P-TK414-flow-filter-2026-10-27.md. Сырьё сделок не сохраняется:
поток -> поминутные массивы в памяти -> признаки на событие -> csv. Сделки: Gate download.gatedata.org, Bybit public.bybit.com (как tk414-aggr.py).
Использование: tk414-flow.py <корень свечей> <legs-слоя2.csv> <gate-list.tsv> <episodes.csv> <выход-префикс> [процессов=4]
Выход: <pref>-events.csv (событие + imb/surge в W и W5 + нога эталона), <pref>-legs.csv (эталон: t0,t1,цены)."""
import csv, gzip, importlib.util, io, os, sys, time, urllib.request, urllib.error
from calendar import timegm
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, fn):
    s = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


T13 = _load("tk413", "tk413-episodes.py")
DET = _load("tk414det", "tk414-detector.py")
M0 = timegm((2026, 8, 1, 0, 0, 0)) // 60  # минута начала сентября
NM = 31 * 1440  # с запасом на 1 октября
BASE, MINHIST = 1440, 360
ZB = 0.01  # порог зигзага для дна паузы (протокол P-TK414-flow-window)
VARIANTS = {"5": (0.05, False), "5half": (0.05, True)}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")


def opener(url):
    for a in range(6):
        try:
            return urllib.request.urlopen(url, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(10 * (a + 1) if e.code in (403, 429) else 3 * (a + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3 * (a + 1))
    raise RuntimeError("не вышло: " + url)


def stream(ex, mk, ins, quanto, feed):
    """Читает сделки сентября потоком, feed(ts_с, цена, кол-во, buy). Возвращает список отсутствующих файлов."""
    miss = []
    if ex == "gate":
        u = "futures_usdt/trades" if mk == "futures_usdt" else "spot/deals"
        r = opener("https://download.gatedata.org/%s/202609/%s-202609.csv.gz" % (u, ins))
        if r is None:
            return ["202609"]
        with gzip.open(r, "rt", encoding="ascii", newline="") as f:
            for line in f:
                p = line.split(",")
                try:
                    if mk == "futures_usdt":
                        s = float(p[3])
                        feed(float(p[0]), float(p[2]), abs(s) * quanto, s > 0)
                    else:
                        feed(float(p[0]), float(p[2]), float(p[3]), p[4].strip() == "buy")
                except (ValueError, IndexError):
                    pass
        return miss
    loc = os.environ.get("TK_BYBIT_LOCAL")  # зеркало TK-417: <loc>/bybit-linear/<СИМ>/<СИМ><дата>.csv.gz, <loc>/bybit-spot/<СИМ>/<СИМ>-2026-09.csv.gz
    if loc:
        if mk == "linear":
            files = [("%s/bybit-linear/%s/%s%s.csv.gz" % (loc, ins, ins, (datetime(2026, 8, 1) + timedelta(days=k)).date().isoformat()), "l")
                     for k in range(31)]
        else:
            files = [("%s/bybit-spot/%s/%s-2026-08.csv.gz" % (loc, ins, ins), "s")]
        for fn, kind in files:
            if not os.path.exists(fn):
                miss.append(os.path.basename(fn))
                continue
            with gzip.open(fn, "rt", encoding="ascii", newline="") as f:
                f.readline()
                for line in f:
                    p = line.split(",")
                    try:
                        if kind == "l":  # timestamp(с),symbol,side,size,price
                            feed(float(p[0]), float(p[4]), float(p[3]), p[2] == "Buy")
                        else:  # id,timestamp(мс),price,volume,side
                            feed(float(p[1]) / 1000, float(p[2]), float(p[3]), p[4].strip().lower() == "buy")
                    except (ValueError, IndexError):
                        pass
        return miss
    d = datetime(2026, 8, 1).date()
    while d <= datetime(2026, 8, 31).date():
        ds = d.isoformat()
        if mk == "linear":
            url = "https://public.bybit.com/trading/%s/%s%s.csv.gz" % (ins, ins, ds)
        else:
            url = "https://public.bybit.com/spot/%s/%s_%s.csv.gz" % (ins, ins, ds)
        r = opener(url)
        if r is None:
            miss.append(ds)
        else:
            with gzip.open(io.BytesIO(r.read()), "rt", encoding="ascii", newline="") as f:
                f.readline()
                for line in f:
                    p = line.split(",")
                    try:
                        if mk == "linear":  # timestamp(с),symbol,side,size,price
                            feed(float(p[0]), float(p[4]), float(p[3]), p[2] == "Buy")
                        else:  # id,timestamp(мс),price,volume,side
                            feed(float(p[1]) / 1000, float(p[2]), float(p[3]), p[4].strip().lower() == "buy")
                    except (ValueError, IndexError):
                        pass
        d += timedelta(days=1)
    return miss


def med(x):
    y = sorted(x)
    return y[len(y) // 2] if y else 0.0


def work(arg):
    root, ex, mk, sym, quanto, bad = arg
    try:
        t, c, q, n, w = T13.load(root, ex, mk, sym)
    except FileNotFoundError:
        return [], [], (ex, mk, sym, "нет свечей")
    t, c, q, n, w, _ = T13.despike(t, c, q, n, w)
    if len(c) < 100:
        return [], [], (ex, mk, sym, "мало свечей")
    buy = [0.0] * NM
    sell = [0.0] * NM
    cnt = [0] * NM

    def feed(ts, px, qty, b):
        m = int(ts // 60) - M0
        if 0 <= m < NM:
            if b:
                buy[m] += px * qty
            else:
                sell[m] += px * qty
            cnt[m] += 1

    miss = stream(ex, mk, sym, quanto, feed)
    tot = [buy[i] + sell[i] for i in range(NM)]
    legs = T13.zigzag(c, DET.THETA)
    lrows, erows = [], []
    refs = {1: [], -1: []}
    for (a, b, s, done) in legs:
        key = (ex, mk, sym, "памп" if s == 1 else "дамп", iso(t[a]))
        bd = key in bad
        refs[s].append((a, b, key[4], bd))
        lrows.append([ex, mk, sym, s, iso(t[a]), iso(t[b]), c[a], c[b], int(done), int(bd)])

    def feats(m_lo, m_hi, s):
        B = sum(buy[m_lo:m_hi + 1])
        S = sum(sell[m_lo:m_hi + 1])
        nt = int(sum(cnt[m_lo:m_hi + 1]))
        if B + S <= 0:
            return "", "", nt
        imb = s * (B - S) / (B + S)
        if m_lo < MINHIST:
            return "%.4f" % imb, "", nt
        hist = tot[m_lo - BASE:m_lo] if m_lo >= BASE else tot[:m_lo]
        base = med(hist)
        if base <= 0:
            base = sum(hist) / len(hist)
        sur = ((B + S) / (m_hi - m_lo + 1)) / base if base > 0 else None
        return "%.4f" % imb, ("%.3f" % sur if sur is not None else ""), nt

    for var, (th, half) in VARIANTS.items():
        for (i, s, ext) in DET.online(c, th, half):
            mi = int(t[i] // 60000) - M0
            me = int(t[ext] // 60000) - M0
            if not (0 <= me <= mi < NM):
                continue
            imb, sur, nt = feats(me, mi, s)
            imb5, sur5, nt5 = feats(max(me, mi - 4), mi, s)
            # B: старт окна = начало последней ноги знака s зигзага 1 % на закрытиях [ext, i]; нет такой ноги -> ext
            zl = [z for z in T13.zigzag(c[ext:i + 1], ZB) if z[2] == s]
            mb = int(t[ext + zl[-1][0]] // 60000) - M0 if zl else me
            imbB, surB, ntB = feats(mb, mi, s)
            imb15, sur15, nt15 = feats(max(me, mi - 14), mi, s)
            imb30, sur30, nt30 = feats(max(me, mi - 29), mi, s)

            def inleg(sg):
                for (a, b, k, bd) in refs[sg]:
                    if a <= i <= b:
                        return k, int(bd)
                return "", 0

            ls, bs = inleg(s)
            lo, bo = inleg(-s)
            erows.append([var, ex, mk, sym, iso(t[i]), s, iso(t[ext]), c[i], c[ext], imb, sur, nt, imb5, sur5, nt5, ls, bs, lo, bo, imbB, surB, ntB, mi - mb, imb15, sur15, imb30, sur30])
    return erows, lrows, (ex, mk, sym, ",".join(miss))


def main():
    root, legs2, gl, epf, outp = sys.argv[1:6]
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 4
    quanto = {}
    for line in open(gl, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if p[0] == "fut":
            quanto[p[1]] = float(p[2])
    bad = set()
    with open(epf, newline="") as f:
        for r in csv.DictReader(f):
            if r["порог_%"] == "10.48" and (r["плохой_принт"] == "да" or r["сомнительный"] == "да"):
                bad.add((r["биржа"], r["рынок"], r["инструмент"], r["знак"], r["начало_utc"]))
    ins = sorted({(r["биржа"], r["рынок"], r["инструмент"]) for r in csv.DictReader(open(legs2, encoding="utf-8"))})
    exf = sys.argv[7] if len(sys.argv) > 7 else ""  # фильтр биржи: Bybit с calc отдаёт 403 (бан IP), Gate — отдельно
    jobs = [(root, e, m, s, quanto.get(s, 1.0), bad) for (e, m, s) in ins if not exf or e == exf]
    print("инструментов", len(jobs), flush=True)
    eh = ["вариант", "биржа", "рынок", "инструмент", "t_событие", "знак", "t_оценка_старта", "цена_события", "цена_старта", "imb", "surge",
          "сделок", "imb5", "surge5", "сделок5", "нога_того_же_знака", "нога_плохая", "нога_обратная", "обратная_плохая", "imbB", "surgeB", "сделокB", "минB", "imb15", "surge15", "imb30", "surge30"]
    lh = ["биржа", "рынок", "инструмент", "знак", "t0", "t1", "цена0", "цена1", "завершена", "плохая"]
    with Pool(nproc) as pool, open(outp + "-events.csv", "w", newline="") as fe, open(outp + "-legs.csv", "w", newline="") as fl, \
            open(outp + ".miss.tsv", "w") as fm:
        we = csv.writer(fe)
        we.writerow(eh)
        wl = csv.writer(fl)
        wl.writerow(lh)
        for er, lr, info in pool.imap_unordered(work, jobs):
            we.writerows(er)
            wl.writerows(lr)
            fm.write("\t".join(info) + "\n")
            fe.flush(); fl.flush(); fm.flush()


if __name__ == "__main__":
    main()
