#!/usr/bin/env python3
"""TK-483: гейт сдачи спот-свечей Bybit. Читает файлы целиком (битый gzip = провал), ничего не пишет кроме отчёта на stdout.
Проверки: (1) число файлов == число cov/*.tsv со строками > 0, у каждого строк в файле == строк в cov, первая/последняя минута совпали;
(2) время строго по возрастанию, на минуте (кратно 60000), без дублей; (3) coverage.csv: строк == строк universe.csv, у каждой
статус «есть» ↔ файл лежит, «нет в архиве» ↔ файла нет; (4) сверка с живым REST: по SAMPLE монет (первые по алфавиту среди «есть»)
минуты 1000 свечей около середины истории == файл (o,h,l,c,v,quote_v).
rc 0 = всё зелёное, иначе 1 и список нарушений. usage: gate.py <w> <каталог данных> [SAMPLE=3]
  w — /data/tk0483 (universe.csv, coverage.csv); каталог данных — /srv/alpha-sda/candles-1m-483 (bybit-spot/, cov/)"""
import csv, gzip, json, os, sys, urllib.request


def read_file(path):
    t, rows = [], []
    with gzip.open(path, "rt", encoding="ascii") as g:
        head = g.readline().rstrip("\n")
        if head != "t_open_ms,o,h,l,c,v,quote_v,n_trades":
            raise ValueError("шапка: " + head)
        for ln in g:
            p = ln.rstrip("\n").split(",")
            t.append(int(p[0])); rows.append(p[1:7])
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


def main():
    w, data = sys.argv[1], sys.argv[2]
    sample = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    bad, nfiles, ok_syms = [], 0, []
    cov = {}
    for fn in sorted(os.listdir(data + "/cov")):
        p = open(data + "/cov/" + fn).read().rstrip("\n").split("\t")
        cov[p[0]] = (int(p[1]), p[2], p[3])
    have = {f[:-7] for f in os.listdir(data + "/bybit-spot") if f.endswith(".csv.gz")}
    want = {s for s, c in cov.items() if c[0] > 0}
    if have != want:
        bad.append("файлы≠cov: только файл %s; только cov %s" % (sorted(have - want)[:5], sorted(want - have)[:5]))
    for sym in sorted(have & want):
        nfiles += 1
        try:
            t, rows = read_file("%s/bybit-spot/%s.csv.gz" % (data, sym))
        except Exception as e:  # noqa: BLE001 — любая порча файла = нарушение символа
            bad.append("%s: %s %s" % (sym, type(e).__name__, str(e)[:80])); continue
        if len(t) != cov[sym][0] or str(t[0]) != cov[sym][1] or str(t[-1]) != cov[sym][2]:
            bad.append("%s: строк %d/%d, первая %d/%s, последняя %d/%s" % (sym, len(t), cov[sym][0], t[0], cov[sym][1], t[-1], cov[sym][2]))
        bad += ["%s: %s" % (sym, b) for b in check_times(t)]
        if len(ok_syms) < sample:
            ok_syms.append((sym, t, rows))
    nu = sum(1 for _ in csv.DictReader(open(w + "/universe.csv")))
    cr = list(csv.DictReader(open(w + "/coverage.csv")))
    if len(cr) != nu:
        bad.append("coverage.csv %d строк, universe.csv %d" % (len(cr), nu))
    for r in cr:
        exists = r["spot_symbol"] in have
        if (r["status"] == "есть") != exists:
            bad.append("%s %s: статус «%s», файл %s" % (r["perp_exchange"], r["perp_symbol"], r["status"], "лежит" if exists else "нет"))
    for sym, t, rows in ok_syms:
        b = rest_window(sym, t, rows)
        if b:
            bad.append(b)
    print("файлов %d, перпов %d, сверка REST %s, нарушений %d" % (nfiles, nu, [s for s, _, _ in ok_syms], len(bad)))
    for b in bad[:50]:
        print("FAIL", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
