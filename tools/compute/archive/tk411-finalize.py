#!/usr/bin/env python3
"""TK-411: сводка охвата свечей 1 м за 2026-09 -> csv (биржа,рынок,инструмент,минут_с_данными,окно_мин,первая_мс,последняя_мс,причина_пропусков).
Источники: <root>/cov/*.tsv (Gate: «рынок инструмент минут сделок мусор»; Bybit: «рынок символ минут первая последняя запросов»),
cov/bybit-instruments.tsv (launch/delivery), heads.tsv Gate («рынок инструмент http байт», 404 -> нет архива).
Использование: tk411-finalize.py <root> <heads.tsv> <выход.csv>; печатает итоги по биржам/рынкам и размер свечей.
"""
import csv
import glob
import gzip
import os
import sys

START = int(os.environ.get("K_START", 1788220800000))  # август (TK-423): K_START=1785542400000 K_END=1788220800000
END = int(os.environ.get("K_END", 1790812800000))
MIN = 60000


def gate_first_last(path):
    first = last = ""
    with gzip.open(path, "rt") as f:
        f.readline()
        first = f.readline().split(",")[0]
        last = first
        for line in f:
            last = line
        last = last.split(",")[0] if isinstance(last, str) else last
    return first, last


def file_stats(path):
    """Минут, первая и последняя t_open_ms по самому файлу свечей (cov-файл мог устареть)."""
    with gzip.open(path, "rb") as f:
        lines = f.read().split(b"\n")
    rows = [x for x in lines[1:] if x]
    return len(rows), (rows[0].split(b",")[0].decode() if rows else ""), (rows[-1].split(b",")[0].decode() if rows else "")


def reason(n, lo, hi, first, last, why):
    r = []
    if first and int(first) > lo:
        r.append("первая свеча %s (листинг/первая сделка позже начала окна)" % ts(first))
    if last and int(last) < hi - MIN:
        r.append("последняя свеча %s (делистинг/экспирация/нет сделок после)" % ts(last))
    if first and last:
        inner = (int(last) - int(first)) // MIN + 1 - n
        if inner > 0:
            r.append("минут без сделок внутри: %d (не выдумывались)" % inner)
    if why:
        r.append(why)
    return "; ".join(r) if r else "полный месяц"


def ts(ms):
    import datetime as d
    return d.datetime.fromtimestamp(int(ms) / 1000, d.timezone.utc).strftime("%d.%m %H:%M")


def main():
    root, heads, out = sys.argv[1:4]
    rows = []
    gate_ok = set()
    for p in sorted(glob.glob(root + "/cov/fut-*.tsv") + glob.glob(root + "/cov/spot-*.tsv")):
        k, c, n, ntr, bad = open(p).read().split("\t")
        n = int(n)
        dst = "%s/candles/%s/%s.csv.gz" % (root, k, c)
        first, last = gate_first_last(dst)
        gate_ok.add((k, c))
        rows.append(["gate", "futures_usdt" if k == "fut" else "spot", c, n, (END - START) // MIN, first, last,
                     reason(n, START, END, first, last, "нет сделок за месяц" if n == 0 else "")])
    for line in open(heads, encoding="utf-8"):
        k, c, code, *_ = (line.rstrip("\n").split("\t") + ["", ""])[:4]
        if (k, c) not in gate_ok:
            rows.append(["gate", "futures_usdt" if k == "fut" else "spot", c, 0, (END - START) // MIN, "", "",
                         "нет месячного архива сделок (HTTP %s)" % code if code != "200" else "архив есть, свечи не собраны"])
    inst = {}
    for line in open(root + "/cov/bybit-instruments.tsv", encoding="utf-8").read().splitlines()[1:]:
        m, s, la, de, st, win = line.split("\t")
        inst[(m, s)] = (int(la), int(de), st, int(win))
    seen = set()
    tl = root + "/cov/bybit-trades-list.txt"  # контракты, собранные из архива сделок public.bybit.com (API свечей закрытых не отдаёт)
    from_trades = set(open(tl).read().split()) if os.path.exists(tl) else set()
    for p in sorted(glob.glob(root + "/cov/bybit-*-*.tsv")):
        if p.endswith(("instruments.tsv", "trades-missing.tsv")):
            continue
        m, s, n, first, last, req = open(p).read().rstrip("\n").split("\t")
        n = int(n)
        dst = "%s/candles/bybit-%s/%s.csv.gz" % (root, m, s)
        if os.path.exists(dst):
            n, first, last = file_stats(dst)
        seen.add((m, s))
        la, de, st, _ = inst[(m, s)]
        lo = max(START, (la // MIN) * MIN)
        hi = min(END, de) if de else END
        win = max(0, (hi - lo) // MIN)
        why = "" if n else "API не вернул свечей (контракт %s, поставка/закрытие %s: история закрытых не отдаётся)" % (st, ts(de) if de else "-")
        if s in from_trades and m == "linear":
            why = "собрано из архива сделок public.bybit.com/trading (API свечей закрытых контрактов не отдаёт; n_trades — число сделок)"
        r = reason(n, lo, hi, first, last, why)
        if first and int(first) > lo:
            r = r.replace("первая свеча", "первая свеча (запуск %s)" % ts(la) if la > START else "первая свеча", 1)
        rows.append(["bybit", m, s, n, win, first, last, r])
    for (m, s), (la, de, st, win) in sorted(inst.items()):
        if (m, s) not in seen:
            rows.append(["bybit", m, s, 0, 0, "", "", "вне окна: запуск %s, поставка/закрытие %s (%s)" % (ts(la) if la else "-", ts(de) if de else "-", st)])
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["биржа", "рынок", "инструмент", "минут_с_данными", "окно_мин", "первая_мс", "последняя_мс", "причина_пропусков"])
        w.writerows(rows)
    agg = {}
    for r in rows:
        a = agg.setdefault((r[0], r[1]), [0, 0, 0, 0])
        a[0] += 1
        a[1] += r[3] > 0
        a[2] += r[3]
    for key, a in sorted(agg.items()):
        print("%s\t%s\tинструментов %d\tсо свечами %d\tминут %d" % (key[0], key[1], a[0], a[1], a[2]))
    tot = {}
    for d in glob.glob(root + "/candles/*"):
        if os.path.isdir(d):
            tot[os.path.basename(d)] = sum(os.path.getsize(x) for x in glob.glob(d + "/*.csv.gz"))
    for k, v in sorted(tot.items()):
        print("размер %s: %.2f ГБ" % (k, v / 1e9))


main()
