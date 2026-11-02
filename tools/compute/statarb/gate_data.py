#!/usr/bin/env python3
"""Ворота данных П-32 §4 (TK-484): один скрипт, результат — файлы, до первого торгового месяца.

  probe  — цена закрытия минуты 12:00 UTC по суткам на символ (один проход по csv.gz) → daily-<биржа>.csv
  build  — сопоставление символов между биржами с множителями (symmap), вселенная по дням со
           снятыми (universe), колено Gate по Kneedle, исключения Д-2 и окна → P-32-params-<дата>.csv

Пороги не назначаются: порог «после поправки |отн.−1| мало» и доля минут со сделкой у Gate берутся
по колену (Kneedle) распределения, измеренного здесь, и пишутся в params до счёта. Только stdlib.
"""
import argparse
import csv
import datetime as dt
import gzip
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from multiprocessing import Pool

MIN_DAY = 1440
NOON = 720  # минута суток 12:00 UTC — зонд цены

_MULT_RE = re.compile(r"^(10+)(?=[A-Z])")
_QUOTE_RE = re.compile(r"_?USDT$")


def split_symbol(sym):
    """`1000PEPEUSDT`/`PEPE_USDT` → (база, множитель в имени): 1000, 'PEPE'. База без префикса 10^k.

    `1INCH` множителя не имеет (префикс «1» без нуля — часть имени); неоднозначные имена
    (`100X`) отсекает сверка цен, а не имя.
    """
    base = _QUOTE_RE.sub("", sym)
    m = _MULT_RE.match(base)
    if m:
        return base[len(m.group(1)):], int(m.group(1))
    return base, 1


def kneedle(values):
    """Колено Kneedle: индекс и значение на кривой, упорядоченной по убыванию.

    Нормировка x,y в [0,1]; разность кривой и хорды (1−x). Выпуклая/вогнутая форма определяется по
    знаку суммы разностей. Возвращает (индекс, значение, форма) или None при <3 точках / плоской кривой.
    """
    ys = sorted(values, reverse=True)
    n = len(ys)
    if n < 3 or ys[0] == ys[-1]:
        return None
    lo, hi = ys[-1], ys[0]
    yn = [(y - lo) / (hi - lo) for y in ys]
    diff = [yn[i] - (1.0 - i / (n - 1)) for i in range(n)]
    concave = sum(diff) >= 0
    key = diff if concave else [-d for d in diff]
    i = max(range(n), key=lambda j: key[j])
    return i, ys[i], "concave" if concave else "convex"


def read_daily(path):
    """daily-<биржа>.csv (symbol,day,close12) → {символ: {день: close}}."""
    out = defaultdict(dict)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]][r["day"]] = float(r["close12"])
    return out


def median_ratio(da, db):
    """Медиана c_a/c_b по общим суткам; (медиана, число суток) или None."""
    days = da.keys() & db.keys()
    rs = [da[d] / db[d] for d in days if da[d] > 0 and db[d] > 0]
    if not rs:
        return None
    return statistics.median(rs), len(rs)


def build_symmap(daily):
    """daily: {биржа: {символ: {день: close}}} → строки сопоставления для всех пар бирж с общей базой."""
    by_base = defaultdict(lambda: defaultdict(list))
    for ex, syms in daily.items():
        for s in syms:
            b, m = split_symbol(s)
            by_base[b][ex].append((s, m))
    rows = []
    exs = sorted(daily)
    for base in sorted(by_base):
        for i, ea in enumerate(exs):
            for eb in exs[i + 1:]:
                for sa, ma in by_base[base].get(ea, []):
                    for sb, mb in by_base[base].get(eb, []):
                        mr = median_ratio(daily[ea][sa], daily[eb][sb])
                        if mr is None:
                            continue
                        r, n = mr
                        k = round(math.log10(r))
                        dev = abs(r / 10 ** k - 1.0)
                        rows.append(dict(base=base, exch_a=ea, sym_a=sa, exch_b=eb, sym_b=sb,
                                         name_ratio=ma / mb, median_ratio=r, k=k, dev=dev,
                                         n_days=n))
    return rows


def dev_threshold(rows):
    """Порог |отн.−1| по колену Kneedle отсортированных dev кандидатов; None, если мало точек.

    Линейная шкала (как у колена Gate): на логарифме колено ложится внутрь массы настоящих совпадений
    (замер 03.11: 0,195 % отсёк 84 пары с dev 0,2…0,6 %), на линейной — на границу хвоста.
    """
    kn = kneedle([r["dev"] for r in rows])
    if kn is None:
        return None
    return kn[1]


def accept_rows(rows, thr):
    for r in rows:
        r["mult"] = 10 ** r["k"]
        r["accepted"] = int(thr is not None and r["dev"] <= thr)
    return rows


def gate_knee(coverage_rows, gate_symbols):
    """Колено доли минут со сделкой по вселенной Gate: coverage.csv (строк / ожидаемо_минут)."""
    shares = []
    for r in coverage_rows:
        if r["биржа"] != "gate-futures_usdt" or r["символ"] not in gate_symbols:
            continue
        exp = int(r["ожидаемо_минут"] or 0)
        if exp > 0 and not r.get("ошибка"):
            shares.append(int(r["строк"]) / exp)
    kn = kneedle(shares)
    return len(shares), kn


def read_universe(path):
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out.append(r)
    return out


def universe_with_closing(universe, sources):
    """Вселенная по дням со снятыми: интервалы как есть; для снятых — день закрытия позиции.

    Монета остаётся во вселенной до `to_day` последнего интервала; если у неё нет свечей позже
    `last_candle_day` (снята), позиция закрывается по последней цене в `closing_day` =
    min(to_day последнего интервала, last_candle_day) — убыток считается, обрыва нет.
    """
    last_day = {}
    for s in sources:
        last_day[(s["exchange"], s["symbol"])] = s.get("last_day") or ""
    horizon = {}
    for (ex, _), d in last_day.items():
        if d and d > horizon.get(ex, ""):
            horizon[ex] = d
    last_iv = {}
    for r in universe:
        key = (r["exchange"], r["symbol"])
        if key not in last_iv or r["to_day"] > last_iv[key]:
            last_iv[key] = r["to_day"]
    out = []
    for r in universe:
        key = (r["exchange"], r["symbol"])
        ld = last_day.get(key, "")
        delisted = int(bool(ld) and ld < horizon.get(r["exchange"], "")
                       and r["to_day"] == last_iv[key])
        closing = min(r["to_day"], ld) if (delisted and ld) else ""
        out.append(dict(exchange=r["exchange"], symbol=r["symbol"], from_day=r["from_day"],
                        to_day=r["to_day"], last_candle_day=ld, delisted=delisted,
                        closing_day=closing))
    return out


def _probe_one(args):
    path, sym = args
    zcat = shutil.which("zcat")
    p = subprocess.Popen([zcat, path], stdout=subprocess.PIPE) if zcat else None
    out = []
    for line in (p.stdout if p else gzip.open(path, "rb")):
        if not line[:1].isdigit():
            continue
        ts = int(line[:13])
        if (ts // 60000) % MIN_DAY != NOON:
            continue
        f = line.split(b",")
        day = dt.datetime.fromtimestamp(ts // 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        out.append((sym, day, f[4].strip().decode()))
    if p:
        p.wait()
    return out


def cmd_probe(a):
    names = sorted(n for n in os.listdir(a.dir) if n.endswith(".csv.gz"))
    jobs = [(os.path.join(a.dir, n), n[:-7]) for n in names]
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "day", "close12"])
        if a.jobs <= 1:
            for j in jobs:
                w.writerows(_probe_one(j))
        else:
            with Pool(a.jobs) as pool:
                for rows in pool.imap(_probe_one, jobs, chunksize=1):
                    w.writerows(rows)
    print(f"probe {a.dir}: {len(jobs)} символов → {a.out}")


def write_csv(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def cmd_build(a):
    exs = a.exchanges.split(",")
    daily = {ex: read_daily(os.path.join(a.daily_dir, f"daily-{ex}.csv")) for ex in exs}
    rows = build_symmap(daily)
    thr = dev_threshold(rows)
    accept_rows(rows, thr)
    cols = ["base", "exch_a", "sym_a", "exch_b", "sym_b", "name_ratio", "median_ratio", "k", "mult",
            "dev", "n_days", "accepted"]
    write_csv(os.path.join(a.out_dir, f"P-32-symmap-{a.date}.csv"), rows, cols)

    uni, src = [], []
    for ex in exs:
        uni += read_universe(os.path.join(a.universe_dir, f"universe-{ex}.csv"))
        src += read_universe(os.path.join(a.universe_dir, f"sources-{ex}.csv"))
    uc = universe_with_closing(uni, src)
    write_csv(os.path.join(a.out_dir, f"P-32-universe-{a.date}.csv"), uc,
              ["exchange", "symbol", "from_day", "to_day", "last_candle_day", "delisted",
               "closing_day"])

    gate_ex = "gate-futures_usdt"
    gate_syms = {r["symbol"] for r in uni if r["exchange"] == gate_ex}
    with open(a.coverage, newline="") as f:
        cov = list(csv.DictReader(f))
    n_g, kn = gate_knee(cov, gate_syms)

    params = [("symmap_candidates", len(rows), "build_symmap"),
              ("symmap_dev_threshold", "" if thr is None else f"{thr:.6g}",
               "kneedle(dev) по кандидатам, линейная шкала"),
              ("symmap_accepted", sum(r["accepted"] for r in rows), "dev <= порог")]
    if kn:
        params += [("gate_share_symbols", n_g, "вселенная Gate (universe) × coverage"),
                   ("gate_share_knee_index", kn[0], f"Kneedle, форма {kn[2]}"),
                   ("gate_share_threshold", f"{kn[1]:.6g}", "доля минут со сделкой на колене")]
    for tag in a.exclude_absent or []:
        k, _, v = tag.partition("=")
        params.append((k, v, "исключение Д-2 (учтено в счётчике)"))
    for win in a.exclude_window or []:
        params.append(("exclude_window", win, "биржа:от_UTC:до_UTC (минуты исключаются)"))
    with open(os.path.join(a.out_dir, f"P-32-params-{a.date}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "value", "source"])
        w.writerows(params)
    print(f"symmap {len(rows)} кандидатов, порог dev {thr}, gate-колено {kn}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--jobs", type=int, required=True)
    p.set_defaults(fn=cmd_probe)
    b = sub.add_parser("build")
    b.add_argument("--daily-dir", required=True)
    b.add_argument("--universe-dir", required=True)
    b.add_argument("--coverage", required=True)
    b.add_argument("--out-dir", required=True)
    b.add_argument("--date", required=True)
    b.add_argument("--exchanges", default="binance-um,bybit-linear,gate-futures_usdt")
    b.add_argument("--exclude-absent", action="append", help="ключ=значение (Д-2)")
    b.add_argument("--exclude-window", action="append", help="биржа:от:до (UTC)")
    b.set_defaults(fn=cmd_build)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
