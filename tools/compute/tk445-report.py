#!/usr/bin/env python3
"""TK-445: сводка закачки сделок и свечей Bybit по месяцам/рынкам/монетам.
Использование: tk445-report.py <корень /data/tk445>. Печатает TSV-таблицы (stdout)."""
import calendar
import collections
import gzip
import os
import sys

root = sys.argv[1]


def load(listf, outd):
    miss = set()
    mf = os.path.join(outd, "_missing.tsv")
    if os.path.exists(mf):
        for ln in open(mf, encoding="utf-8"):
            f = ln.rstrip("\n").split(" ")
            miss.add((f[1], f[2], f[3]))
    fails = set()
    ff = os.path.join(outd, "_fails.tsv")
    if os.path.exists(ff):
        for ln in open(ff, encoding="utf-8"):
            f = ln.split("\t")[0].split(" ")
            fails.add((f[1], f[2], f[3]))
    rows = []
    for ln in open(listf, encoding="utf-8"):
        mk, sym, url, rel = ln.rstrip("\n").split("\t")
        base = os.path.basename(rel)
        p = os.path.join(outd, rel)
        # месяц из имени: SYM-YYYY-MM.csv.gz (spot) | SYMYYYY-MM-DD.csv.gz (linear)
        mon = base[:-7][len(sym):].lstrip("-")[:7]
        sz = os.path.getsize(p) if os.path.exists(p) else None
        st = "ok" if sz is not None else ("404" if (mk, sym, base) in miss else ("fail" if (mk, sym, base) in fails else "нет"))
        rows.append((mon, mk, sym, st, sz or 0))
    return rows


def table(rows, title):
    print("## " + title)
    agg = collections.defaultdict(collections.Counter)
    for mon, mk, sym, st, sz in rows:
        a = agg[(mon, mk)]
        a["всего"] += 1
        a[st] += 1
        a["байт"] += sz
    print("месяц\tрынок\tзадач\tok\t404\tfail\tнет\tГБ")
    for (mon, mk), a in sorted(agg.items()):
        print("%s\t%s\t%d\t%d\t%d\t%d\t%d\t%.2f" % (mon, mk, a["всего"], a["ok"], a["404"], a["fail"], a["нет"], a["байт"] / 1e9))


aug = load(os.path.join(root, "aug.tsv"), os.path.join(root, "aug"))
table(aug, "август 2026 (окна событий ±1 сут + сдвиги)")
past = load(os.path.join(root, "past.tsv"), os.path.join(root, "past"))
table(past, "прошлые годы: сделки")
print("## прошлые годы: сделки по монете (месяц, рынок, символ, ok/задач, ГБ)")
per = collections.defaultdict(lambda: [0, 0, 0])
for mon, mk, sym, st, sz in past:
    p = per[(mon, mk, sym)]
    p[0] += st == "ok"
    p[1] += 1
    p[2] += sz
for (mon, mk, sym), (ok, n, sz) in sorted(per.items()):
    if ok:
        print("%s\t%s\t%s\t%d/%d\t%.3f" % (mon, mk, sym, ok, n, sz / 1e9))
print("## прошлые годы: символы без единого файла (месяц, рынок, символ)")
for (mon, mk, sym), (ok, n, sz) in sorted(per.items()):
    if not ok:
        print("%s\t%s\t%s\t0/%d" % (mon, mk, sym, n))
print("## свечи 1 м (месяц, рынок, символ, строк, ожид. минут)")
cdir = os.path.join(root, "candles")
for mon in sorted(os.listdir(cdir)):
    y, m = map(int, mon.split("-"))
    exp = calendar.monthrange(y, m)[1] * 1440
    for mk in ("linear", "spot"):
        d = os.path.join(cdir, mon, "candles", "bybit-" + mk)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            n = sum(1 for _ in gzip.open(os.path.join(d, fn), "rt")) - 1
            print("%s\t%s\t%s\t%d\t%d" % (mon, mk, fn[:-7], n, exp))
