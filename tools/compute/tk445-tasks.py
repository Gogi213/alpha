#!/usr/bin/env python3
"""TK-445: список закачки сделок Bybit по событиям v11 месяца: linear — дни окон события (t_start..+240 мин) и их сдвигов ±7/14/21 сут (контроли TK-444),
в пределах месяца; spot — месячный файл. Только монеты с парой spot+linear на public.bybit.com.
Использование: tk445-tasks.py <events.csv> <ГГГГ-ММ> <spot-list.txt> <trading-list.txt> <out.tsv> [--sym A,B,..]
Строка выхода: mk<TAB>sym<TAB>url-путь<TAB>относительный путь файла."""
import sys, csv, collections, datetime
evf, month, spf, lnf, out = sys.argv[1:6]
only = set(sys.argv[sys.argv.index("--sym") + 1].split(",")) if "--sym" in sys.argv else None
spot = set(open(spf).read().split()); lin = set(open(lnf).read().split())
y, m = map(int, month.split("-"))
d0 = datetime.date(y, m, 1); d1 = (datetime.date(y + (m == 12), m % 12 + 1, 1))
D0 = d0.toordinal(); D1 = d1.toordinal()
days = collections.defaultdict(set)
for r in csv.DictReader(open(evf, encoding="utf-8")):
    s = r["инструмент"]
    if r["биржа_рынок"] != "bybit-linear" or s not in spot or s not in lin or (only and s not in only):
        continue
    a = int(r["t_start_ms"]); b = a + min(int(r["t_peak_ms"]) - a, 240 * 60000)
    for sh in (0, 7, 14, 21, -7, -14, -21):
        for t in (a + sh * 86400000, b + sh * 86400000):
            o = datetime.date.fromtimestamp(0).toordinal() + t // 86400000
            if D0 <= o < D1:
                days[s].add(o)
n = 0
with open(out, "w") as f:
    for s in sorted(days):
        f.write("spot\t%s\t/spot/%s/%s-%s.csv.gz\tbybit-spot/%s/%s-%s.csv.gz\n" % (s, s, s, month, s, s, month))
        for o in sorted(days[s]):
            t = datetime.date.fromordinal(o).isoformat()
            f.write("linear\t%s\t/trading/%s/%s%s.csv.gz\tbybit-linear/%s/%s%s.csv.gz\n" % (s, s, s, t, s, s, t)); n += 1
print("монет %d, linear-файлов %d, spot-файлов %d" % (len(days), n, len(days)))
