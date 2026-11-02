#!/usr/bin/env python3
"""TK-476 (возврат Судьи 02.11): свечи 1 м снятых монет Bybit из дневного архива сделок public.bybit.com/trading.
Фаза 1 — скачать <SYM><день>.csv.gz в RAW/<SYM>/ (идемпотентно: есть файл — пропуск; tmp+rename; повтор 6 раз).
Фаза 2 — по монете в порядке дней: свечи по правилам, подтверждённым гейтом tk476_bybit_gate.py (REST = архив):
  c = цена последней сделки минуты, v = Σsize, quote_v = Σsize·price, n_trades = число сделок;
  o = c последней сделки ДО минуты (через границу суток; для самой первой минуты — цена первой сделки), h/l — с учётом o.
Минуты без сделок НЕ пишутся (не выдумываем). Выход OUT/<SYM>.csv.gz (t_open_ms,o,h,l,c,v,quote_v,n_trades; o/h/l/c — строки цены архива).
Использование: tk476-bybit-closed.py <список символов> <RAW> <OUT> <метка-каталог для .done/.fail> [потоков=48] [процессов=15]
Итог: <метка>.done при FAIL=0 (все скачано и собрано), иначе <метка>.fail; сводка по монете — OUT/summary.csv.
403/404 на сутки = «нет в архиве» (не провал, без повторов): список <метка>.absent (символ день), повторный запуск эти сутки не запрашивает.
Было (первый запуск 02.11): 403 повторялся 6 раз со сном 2…12 с = 42 с на сутки × 25 336 суток / 48 потоков ≈ 6,2 ч из 7,5 ч скачивания."""
import csv, gzip, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

BASE = "https://public.bybit.com/trading"
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
        except Exception as e:
            err = e
        time.sleep(2 * (i + 1))
    raise err


def listing(sym):
    h = http(f"{BASE}/{sym}/").decode()
    days = sorted(set(re.findall(re.escape(sym) + r"(\d{4}-\d\d-\d\d)\.csv\.gz", h)))
    return [d for d in days if d <= LAST_DAY]


def fetch(job):
    """None — файл есть; ("absent", sym, day) — суток нет в архиве (403/404); строка — провал (повтор запуском)."""
    sym, day, raw = job
    dst = f"{raw}/{sym}/{sym}{day}.csv.gz"
    if os.path.exists(dst):
        return None
    tmp = dst + ".tmp"
    try:
        data = http(f"{BASE}/{sym}/{sym}{day}.csv.gz")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        return None
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return ("absent", sym, day)
        return f"{sym} {day} {type(e).__name__} {e}"
    except Exception as e:
        return f"{sym} {day} {type(e).__name__} {e}"


def candles_day(path, state, out):
    """state = [последняя цена-строка или None]; пишет строки дня в out; возвращает (минут, сделок)."""
    with gzip.open(path, "rt", encoding="ascii", newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        ix = {k: i for i, k in enumerate(hdr)}
        it, ip, iq = ix["timestamp"], ix["price"], ix["size"]
        rows = [(float(r[it]), n, r[ip], float(r[iq])) for n, r in enumerate(rd)]
    rows.sort(key=lambda r: (r[0], r[1]))
    m = {}
    for ts, _, ps, q in rows:
        k = int(ts // 60)
        px = float(ps)
        c = m.get(k)
        if c is None:
            m[k] = {"os": ps, "hf": px, "hs": ps, "lf": px, "ls": ps, "cs": ps, "v": q, "qv": q * px, "n": 1}
        else:
            if px > c["hf"]:
                c["hf"], c["hs"] = px, ps
            if px < c["lf"]:
                c["lf"], c["ls"] = px, ps
            c["cs"] = ps
            c["v"] += q
            c["qv"] += q * px
            c["n"] += 1
    prev = state[0]
    for k in sorted(m):
        c = m[k]
        if prev is None:
            o = c["os"]
        else:
            o = prev
        of = float(o)
        h = c["hs"] if c["hf"] >= of else o
        lo = c["ls"] if c["lf"] <= of else o
        out.write("%d,%s,%s,%s,%s,%.12g,%.12g,%d\n" % (k * 60000, o, h, lo, c["cs"], c["v"], c["qv"], c["n"]))
        prev = c["cs"]
    state[0] = prev
    return len(m), len(rows)


def build(job):
    sym, days, raw, out = job
    dst = f"{out}/{sym}.csv.gz"
    tmp = dst + ".tmp"
    state = [None]
    nmin = ntr = 0
    try:
        with gzip.open(tmp, "wt", encoding="ascii", newline="", compresslevel=6) as o:
            o.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
            for d in days:
                a, b = candles_day(f"{raw}/{sym}/{sym}{d}.csv.gz", state, o)
                nmin += a
                ntr += b
        os.replace(tmp, dst)
        return (sym, len(days), nmin, ntr, "")
    except Exception as e:
        return (sym, len(days), nmin, ntr, f"{type(e).__name__} {e}")


def main():
    symf, raw, out, tag = sys.argv[1:5]
    nthr = int(sys.argv[5]) if len(sys.argv) > 5 else 48
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    syms = [s.strip() for s in open(symf) if s.strip()]
    os.makedirs(out, exist_ok=True)
    for f in (tag + ".done", tag + ".fail"):
        if os.path.exists(f):
            os.remove(f)
    t0 = time.time()
    with ThreadPoolExecutor(16) as ex:
        lst = list(ex.map(listing, syms))
    days = dict(zip(syms, lst))
    for s in syms:
        os.makedirs(f"{raw}/{s}", exist_ok=True)
    absent = set()
    if os.path.exists(tag + ".absent"):
        absent = {tuple(l.split()) for l in open(tag + ".absent") if l.strip()}
    jobs = [(s, d, raw) for s in syms for d in days[s] if (s, d) not in absent]
    print(f"монет {len(syms)} суток {len(jobs)} (известных «нет в архиве» {len(absent)}) листинг {time.time() - t0:.0f} с", flush=True)
    fails = []
    with ThreadPoolExecutor(nthr) as ex:
        for i, r in enumerate(ex.map(fetch, jobs)):
            if isinstance(r, tuple):
                absent.add(r[1:])
            elif r:
                fails.append(r)
                print("FAIL", r, flush=True)
            if (i + 1) % 2000 == 0:
                print(f"скачано {i + 1}/{len(jobs)} за {time.time() - t0:.0f} с, FAIL={len(fails)}", flush=True)
    with open(tag + ".absent", "w") as f:
        for a in sorted(absent):
            f.write("%s %s" % a + chr(10))
    print(f"скачивание: {len(jobs)} суток, нет в архиве {len(absent)}, FAIL={len(fails)}, {time.time() - t0:.0f} с", flush=True)
    for s in syms:
        days[s] = [d for d in days[s] if (s, d) not in absent]
    if fails:
        open(tag + ".fail", "w").write("скачивание FAIL=%d, первые: %s\n" % (len(fails), "; ".join(fails[:5])))
        sys.exit(1)
    res = []
    with ProcessPoolExecutor(nproc) as ex:
        for r in ex.map(build, [(s, days[s], raw, out) for s in syms]):
            res.append(r)
            if r[4]:
                print("FAIL build", r, flush=True)
    with open(f"{out}/summary.csv", "w") as f:
        f.write("symbol,days,minutes,trades,error\n")
        for r in res:
            f.write("%s,%d,%d,%d,%s\n" % r)
    bad = [r for r in res if r[4]]
    print(f"сборка: {len(res)} монет, ошибок {len(bad)}, всего {time.time() - t0:.0f} с", flush=True)
    if bad:
        open(tag + ".fail", "w").write("сборка ошибок %d\n" % len(bad))
        sys.exit(1)
    open(tag + ".done", "w").write("ok %s\n" % time.strftime("%F %T", time.gmtime()))


if __name__ == "__main__":
    main()
