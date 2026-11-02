#!/usr/bin/env python3
"""TK-483 (возврат Судьи 03.11): свечи 1 м снятых пар Bybit spot (REST пуст, 10001) из дневного архива сделок
public.bybit.com/spot/<SYM>/<SYM>_<день>.csv.gz (колонки id,timestamp[мс],price,volume,side). Образец — tk476-bybit-closed.py (linear).
Фаза 1 — скачать сутки в RAW/<SYM>/ (идемпотентно: файл есть — пропуск; tmp+rename; 403/404 = «нет в архиве», без повторов).
Фаза 2 — по паре в порядке суток: c = цена последней сделки минуты (внутри последней мс — конец свипа тейкера: buy max, sell min цены), v = Σvolume, quote_v = Σvolume·price, n_trades = число
сделок; o = c последней сделки ДО минуты (через границу суток; для самой первой минуты — цена первой сделки), h/l — с учётом o.
Неоднозначная минута — в последней мс ≥ 2 разных цен: порядок исполнения внутри мс архив не хранит, закрытие (и открытие следующей) по правилу
свипа может разойтись с REST; число таких минут пара пишет в OUT/closed-summary.csv.
Минуты без сделок НЕ пишутся (не выдумываем). Выход OUT/bybit-spot/<SYM>.csv.gz (формат hist.py: t_open_ms,o,h,l,c,v,quote_v,n_trades)
и OUT/cov/<SYM>.tsv (sym, строк, первая, последняя, суток в архиве) — вместо пустышек hist.py; прежние пустышки — в OUT/prev-empty/.
usage: closed.py <symbols.txt> <RAW> <OUT> <метка для .done/.fail/.absent> [потоков=16] [процессов=3]"""
import csv, gzip, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

BASE = "https://public.bybit.com/spot"
LAST_DAY = "2026-10-31"


def http(url, tries=6):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            err = e
        except Exception as e:  # noqa: BLE001 — сеть: повтор
            err = e
        time.sleep(2 * (i + 1))
    raise err


def listing(sym):
    """Сутки в архиве (daily-файлы SYM_ГГГГ-ММ-ДД; месячные SYM-ГГГГ-ММ — те же сделки, их не берём) до LAST_DAY."""
    h = http("%s/%s/" % (BASE, sym)).decode()
    days = sorted(set(re.findall(re.escape(sym) + r"_(\d{4}-\d\d-\d\d)\.csv\.gz", h)))
    return [d for d in days if d <= LAST_DAY]


def fetch(job):
    """None — файл есть; ("absent", sym, day) — суток нет (403/404); строка — провал (повтор запуском)."""
    sym, day, raw = job
    dst = "%s/%s/%s_%s.csv.gz" % (raw, sym, sym, day)
    if os.path.exists(dst):
        return None
    tmp = dst + ".tmp"
    try:
        data = http("%s/%s/%s_%s.csv.gz" % (BASE, sym, sym, day))
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        return None
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return ("absent", sym, day)
        return "%s %s %s %s" % (sym, day, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001
        return "%s %s %s %s" % (sym, day, type(e).__name__, e)


def candles_day(path, state, out):
    """state = [цена-строка последней сделки или None]; пишет строки суток в out; -> (минут, сделок).
    Закрытие минуты: id внутри одной мс в архиве не в порядке исполнения. REST берёт последней сделку, где свип тейкера кончился:
    buy — максимальная цена последней мс, sell — минимальная (гейт closed_gate.py). Последней мс считается серия сделок одной стороны
    в конце мс (по id); при смешанных сторонах решает сторона сделки с наибольшим id."""
    with gzip.open(path, "rt", encoding="ascii", newline="") as f:
        rd = csv.reader(f)
        ix = {k: i for i, k in enumerate(next(rd))}
        ii, it, ip, iq, isd = ix["id"], ix["timestamp"], ix["price"], ix["volume"], ix["side"]
        rows = [(int(r[it]), int(r[ii]), r[ip], float(r[iq]), r[isd]) for r in rd]
    rows.sort(key=lambda r: (r[0], r[1]))
    m = {}
    for ts, _, ps, q, sd in rows:
        k = ts // 60000
        px = float(ps)
        c = m.get(k)
        if c is None:
            m[k] = {"os": ps, "hf": px, "hs": ps, "lf": px, "ls": ps, "v": q, "qv": q * px, "n": 1,
                    "lt": ts, "sd": sd, "gx": px, "gxs": ps, "gn": px, "gns": ps, "lms": ts, "lp": ps, "amb": False}
        else:
            if px > c["hf"]:
                c["hf"], c["hs"] = px, ps
            if px < c["lf"]:
                c["lf"], c["ls"] = px, ps
            if ts != c["lms"]:
                c["lms"], c["lp"], c["amb"] = ts, ps, False
            elif ps != c["lp"]:
                c["amb"] = True
            if ts != c["lt"] or sd != c["sd"]:  # новая мс или смена стороны: серия последней мс начинается заново
                c["lt"], c["sd"] = ts, sd
                c["gx"], c["gxs"], c["gn"], c["gns"] = px, ps, px, ps
            else:
                if px > c["gx"]:
                    c["gx"], c["gxs"] = px, ps
                if px < c["gn"]:
                    c["gn"], c["gns"] = px, ps
            c["v"] += q
            c["qv"] += q * px
            c["n"] += 1
    prev = state[0]
    amb = set()
    for k in sorted(m):
        c = m[k]
        if c["amb"]:
            amb.add(k * 60000)
        cs = c["gxs"] if c["sd"] == "buy" else c["gns"]
        o = c["os"] if prev is None else prev
        of = float(o)
        h = c["hs"] if c["hf"] >= of else o
        lo = c["ls"] if c["lf"] <= of else o
        out.write("%d,%s,%s,%s,%s,%.12g,%.12g,%d\n" % (k * 60000, o, h, lo, cs, c["v"], c["qv"], c["n"]))
        prev = cs
    state[0] = prev
    return len(m), len(rows), amb


def build(job):
    """-> (sym, суток, минут, сделок, неоднозначных, первая_мс, последняя_мс, ошибка). Пишет OUT/bybit-spot/<sym>.csv.gz и OUT/cov/<sym>.tsv."""
    sym, days, raw, out = job
    dst = "%s/bybit-spot/%s.csv.gz" % (out, sym)
    tmp = dst + ".tmp"
    state = [None]
    nmin = ntr = namb = 0
    try:
        with gzip.open(tmp, "wt", encoding="ascii", newline="", compresslevel=6) as o:
            o.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
            for d in days:
                a, b, am = candles_day("%s/%s/%s_%s.csv.gz" % (raw, sym, sym, d), state, o)
                nmin += a
                ntr += b
                namb += len(am)
        first = last = ""
        with gzip.open(tmp, "rt", encoding="ascii") as g:
            g.readline()
            for ln in g:
                if not first:
                    first = ln.split(",", 1)[0]
                last = ln
        last = last.split(",", 1)[0]
        prev = "%s/prev-empty/%s.csv.gz" % (out, sym)
        os.makedirs(os.path.dirname(prev), exist_ok=True)
        if os.path.exists(dst):
            os.replace(dst, prev)  # прежняя пустышка hist.py (шапка, cov 0)
        os.replace(tmp, dst)
        with open("%s/cov/%s.tsv" % (out, sym), "w") as c:
            c.write("%s\t%d\t%s\t%s\t%d\n" % (sym, nmin, first, last, len(days)))
        return (sym, len(days), nmin, ntr, namb, first, last, "")
    except Exception as e:  # noqa: BLE001
        return (sym, len(days), nmin, ntr, namb, "", "", "%s %s" % (type(e).__name__, e))


def main():
    symf, raw, out, tag = sys.argv[1:5]
    nthr = int(sys.argv[5]) if len(sys.argv) > 5 else 16
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 3
    syms = [s.strip() for s in open(symf) if s.strip()]
    t0 = time.time()
    days, jobs = {}, []
    for s in syms:
        os.makedirs("%s/%s" % (raw, s), exist_ok=True)
        days[s] = listing(s)
        jobs += [(s, d, raw) for d in days[s]]
    print("пар %d, суток в листинге %d" % (len(syms), len(jobs)), flush=True)
    absent, fails = set(), []
    with ThreadPoolExecutor(nthr) as ex:
        for i, r in enumerate(ex.map(fetch, jobs)):
            if isinstance(r, tuple):
                absent.add(r[1:])
            elif r:
                fails.append(r)
            if (i + 1) % 500 == 0:
                print("скачано %d/%d за %.0f с, FAIL=%d" % (i + 1, len(jobs), time.time() - t0, len(fails)), flush=True)
    with open(tag + ".absent", "w") as f:
        for a in sorted(absent):
            f.write("%s %s\n" % a)
    print("скачивание: %d суток, нет в архиве %d, FAIL=%d, %.0f с" % (len(jobs), len(absent), len(fails), time.time() - t0), flush=True)
    for s in syms:
        days[s] = [d for d in days[s] if (s, d) not in absent]
    if fails:
        open(tag + ".fail", "w").write("скачивание FAIL=%d, первые: %s\n" % (len(fails), "; ".join(fails[:5])))
        sys.exit(1)
    os.makedirs(out + "/bybit-spot", exist_ok=True)
    os.makedirs(out + "/cov", exist_ok=True)
    res = []
    with ProcessPoolExecutor(nproc) as ex:
        for r in ex.map(build, [(s, days[s], raw, out) for s in syms]):
            res.append(r)
            print("сборка", r, flush=True)
    with open(out + "/closed-summary.csv", "w") as f:
        f.write("symbol,days,minutes,trades,ambiguous_minutes,first_ms,last_ms,error\n")
        for r in res:
            f.write("%s,%d,%d,%d,%d,%s,%s,%s\n" % r)
    bad = [r for r in res if r[7]]
    print("сборка: %d пар, ошибок %d, всего %.0f с" % (len(res), len(bad), time.time() - t0), flush=True)
    if bad:
        open(tag + ".fail", "w").write("сборка ошибок %d\n" % len(bad))
        sys.exit(1)
    open(tag + ".done", "w").write("ok %s\n" % time.strftime("%F %T", time.gmtime()))


if __name__ == "__main__":
    main()
