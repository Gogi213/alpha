#!/usr/bin/env python3
"""TK-428: охват свечей июля 2026 (01–31.07) склейкой каталогов -> csv того же формата, что tk411-finalize.py.
Bybit: <a>/bybit-<рынок>/<символ>.csv.gz (01–23.07) + <b>/candles/bybit-<рынок>/ (24–31.07, TK-425), список — оба bybit-instruments.tsv;
Gate: строки целиком из <gate_cov> (TK-425, jul-gate/coverage.csv: окно 01–31.07).
Использование: tk428-cov-jul.py <a: candles-1m-202607> <b: tk425/jul> <gate_cov.csv> <выход.csv> [cov-каталоги через запятую]"""
import csv
import datetime as d
import glob
import gzip
import os
import sys

START, END, MIN = 1782864000000, 1785542400000, 60000


def ts(ms):
    return d.datetime.fromtimestamp(int(ms) / 1000, d.timezone.utc).strftime("%d.%m %H:%M")


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


def stamps(path):
    if not os.path.exists(path):
        return set()
    with gzip.open(path, "rb") as f:
        return {int(x.split(b",")[0]) for x in f.read().split(b"\n")[1:] if x}


def main():
    a, b, gate_cov, out = sys.argv[1:5]
    covs = sys.argv[5].split(",") if len(sys.argv) > 5 else [b + "/cov"]
    inst = {}
    for c in covs:
        for line in open(c + "/bybit-instruments.tsv", encoding="utf-8").read().splitlines()[1:]:
            m, s, la, de, st, _ = line.split("\t")
            inst[(m, s)] = (int(la), int(de), st)
    api = set()
    for c in covs:
        for p in glob.glob(c + "/bybit-*-*.tsv"):
            if not p.endswith("instruments.tsv"):
                api.add(tuple(os.path.basename(p)[len("bybit-"):-len(".tsv")].split("-", 1)))
    rows = []
    for (m, s), (la, de, st) in sorted(inst.items()):
        t = stamps("%s/bybit-%s/%s.csv.gz" % (a, m, s)) | stamps("%s/candles/bybit-%s/%s.csv.gz" % (b, m, s))
        n = len(t)
        first, last = (str(min(t)), str(max(t))) if t else ("", "")
        lo = max(START, (la // MIN) * MIN)
        hi = min(END, de) if de else END
        if not n and (m, s) not in api:
            rows.append(["bybit", m, s, 0, 0, "", "", "вне окна: запуск %s, поставка/закрытие %s (%s)" % (ts(la) if la else "-", ts(de) if de else "-", st)])
            continue
        why = "" if n else "API не вернул свечей (контракт %s, поставка/закрытие %s: история закрытых не отдаётся)" % (st, ts(de) if de else "-")
        r = reason(n, lo, hi, first, last, why)
        if first and int(first) > lo and la > START:
            r = r.replace("первая свеча", "первая свеча (запуск %s)" % ts(la), 1)
        rows.append(["bybit", m, s, n, max(0, (hi - lo) // MIN), first, last, r])
    with open(gate_cov, encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        rows += [r for r in rd if r[0] == "gate"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["биржа", "рынок", "инструмент", "минут_с_данными", "окно_мин", "первая_мс", "последняя_мс", "причина_пропусков"])
        w.writerows(rows)
    agg = {}
    for r in rows:
        x = agg.setdefault((r[0], r[1]), [0, 0, 0])
        x[0] += 1
        x[1] += int(r[3]) > 0
        x[2] += int(r[3])
    for k, x in sorted(agg.items()):
        print("%s\t%s\tинструментов %d\tсо свечами %d\tминут %d" % (k[0], k[1], x[0], x[1], x[2]))


main()
