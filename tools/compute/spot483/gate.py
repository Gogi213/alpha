#!/usr/bin/env python3
"""TK-483: гейт сдачи спот-свечей Bybit. Читает файлы целиком (битый gzip = провал), ничего не пишет кроме отчёта на stdout.
Проверки: (1) файлы == cov/*.tsv (пустая пара REST — файл только с шапкой и cov 0 строк, пишет hist.py как отметку «готово»), у каждого
строк в файле == строк в cov, первая/последняя минута совпали;
(2) время строго по возрастанию, на минуте (кратно 60000), без дублей; (3) coverage.csv: строк == строк universe.csv, у каждой
статус «есть» ↔ в файле есть строки, «нет в архиве» ↔ файла нет или он пуст; (4) сверка с живым REST: по SAMPLE монет (первые по алфавиту среди «есть»)
минуты 1000 свечей около середины истории == файл (o,h,l,c,v,quote_v).
rc 0 = всё зелёное, иначе 1 и список нарушений. usage: gate.py <w> <каталог данных> [SAMPLE=3] [процессов=8]
  w — /data/tk0483 (universe.csv, coverage.csv); каталог данных — /srv/alpha-sda/candles-1m-483 (bybit-spot/, cov/)"""
import csv, gzip, json, os, sys, urllib.request
from multiprocessing import Pool


def read_file(path, keep_rows=True):
    t, rows = [], []
    with gzip.open(path, "rt", encoding="ascii") as g:
        head = g.readline().rstrip("\n")
        if head != "t_open_ms,o,h,l,c,v,quote_v,n_trades":
            raise ValueError("шапка: " + head)
        for ln in g:
            p = ln.rstrip("\n").split(",")
            t.append(int(p[0]))
            if keep_rows:
                rows.append(p[1:7])
    return t, rows


def check_times(t):
    """-> список нарушений порядка/минуты по списку времён."""
    bad = []
    for i, x in enumerate(t):
        if x % 60000:
            bad.append("не на минуте %d" % x)
        if i and x <= t[i - 1]:
            bad.append("не по возрастанию/дубль %d" % x)
        if len(bad) >= 3:
            break
    return bad


def rest_window(sym, t, rows):
    """Сверка 1000 свечей около середины файла с REST. -> строка нарушения или None."""
    mid = len(t) // 2
    lo, hi = t[max(0, mid - 500)], t[min(len(t) - 1, mid + 499)]
    url = "https://api.bybit.com/v5/market/kline?category=spot&symbol=%s&interval=1&start=%d&end=%d&limit=1000" % (sym, lo, hi)
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if d.get("retCode") != 0:
        return "%s: REST retCode %s" % (sym, d.get("retCode"))
    got = {int(k[0]): k[1:7] for k in d["result"]["list"]}
    want = {x: rows[i] for i, x in enumerate(t) if lo <= x <= hi}
    if not want or set(got) != set(want):
        return "%s: REST %d свечей, файл %d (%d..%d)" % (sym, len(got), len(want), lo, hi)
    diff = [x for x in want if got[x] != want[x]]
    return "%s: REST≠файл в %d из %d минут (первая %d)" % (sym, len(diff), len(want), diff[0]) if diff else None


def check_symbol(a):
    """Один файл целиком (в процессе пула). a = (путь, символ, (строк, первая, последняя) из cov) -> список нарушений."""
    path, sym, c = a
    try:
        t, _ = read_file(path, keep_rows=False)
    except Exception as e:  # noqa: BLE001 — любая порча файла = нарушение символа
        return ["%s: %s %s" % (sym, type(e).__name__, str(e)[:80])]
    if not t:
        return [] if c == (0, "", "") else ["%s: файл пуст, cov %s" % (sym, c)]
    bad = []
    if len(t) != c[0] or str(t[0]) != c[1] or str(t[-1]) != c[2]:
        bad.append("%s: строк %d/%d, первая %d/%s, последняя %d/%s" % (sym, len(t), c[0], t[0], c[1], t[-1], c[2]))
    return bad + ["%s: %s" % (sym, b) for b in check_times(t)]


def main():
    w, data = sys.argv[1], sys.argv[2]
    sample = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    procs = int(sys.argv[4]) if len(sys.argv) > 4 else 8
    bad = []
    cov = {}
    for fn in sorted(os.listdir(data + "/cov")):
        p = open(data + "/cov/" + fn).read().rstrip("\n").split("\t")
        cov[p[0]] = (int(p[1]), p[2], p[3])
    have = {f[:-7] for f in os.listdir(data + "/bybit-spot") if f.endswith(".csv.gz")}
    if have != set(cov):
        bad.append("файлы≠cov: только файл %s; только cov %s" % (sorted(have - set(cov))[:5], sorted(set(cov) - have)[:5]))
    both = sorted(have & set(cov))
    with Pool(procs) as pool:
        for b in pool.imap(check_symbol, [("%s/bybit-spot/%s.csv.gz" % (data, s), s, cov[s]) for s in both], chunksize=4):
            bad += b
    with_rows = {s for s in both if cov[s][0] > 0}
    nu = sum(1 for _ in csv.DictReader(open(w + "/universe.csv")))
    cr = list(csv.DictReader(open(w + "/coverage.csv")))
    if len(cr) != nu:
        bad.append("coverage.csv %d строк, universe.csv %d" % (len(cr), nu))
    for r in cr:
        exists = r["spot_symbol"] in with_rows
        if (r["status"] == "есть") != exists:
            bad.append("%s %s: статус «%s», строк в файле %s" % (r["perp_exchange"], r["perp_symbol"], r["status"], "есть" if exists else "нет"))
    syms = sorted(with_rows)[:sample]
    for sym in syms:
        t, rows = read_file("%s/bybit-spot/%s.csv.gz" % (data, sym))
        b = rest_window(sym, t, rows)
        if b:
            bad.append(b)
    print("файлов %d (с данными %d), перпов %d, сверка REST %s, нарушений %d" % (len(both), len(with_rows), nu, syms, len(bad)))
    for b in bad[:50]:
        print("FAIL", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
