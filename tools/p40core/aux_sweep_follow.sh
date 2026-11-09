#!/bin/bash
# TK-512: по окончании p40-d1-gap2 — добить перегон aux (старый вид -> новый), пересобрать out_d1/auxholes.csv, написать отчёт
while systemctl is-active -q p40-d1-gap2; do sleep 60; done
python3 /root/tk512/live/aux_sweep.py --final > /root/tk512/live/follow.log 2>&1 || { echo "СБОЙ sweep rc $?" >> /root/tk512/live/follow.log; exit 1; }
cd /root/p40core/out_d1
python3 - > /root/tk512/live/final-report.txt <<"PY"
import glob, csv
pairs = {(r["asset"], r["pair"]): r for r in csv.DictReader(open("../pairs_d1.csv"))}
tot = old = ast = astnew = 0; assets = set(); oldl = []
for f in sorted(glob.glob("*/*/*.aux.csv")):
    a = f.split("/")[0]; p = f.split("/")[-1][:-8]; r = pairs[(a, p)]
    h = "srcOIA" in open(f).readline(); tot += 1
    if not h: old += 1; oldl.append(f)
    if "aster" in (r["exA"], r["exB"]): ast += 1; astnew += h; assets.add(a)
print("aux.csv всего", tot, "| старый вид", old, oldl[:10], "| пар с Aster", ast, "новый вид", astnew, "активов с Aster", len(assets))
n = sum(1 for x in csv.reader(open("auxholes.csv"))) - 1
k = sum(1 for x in csv.reader(open("auxholes.csv")) if x[0] == "aster" and x[4] == "open_interest")
print("auxholes.csv строк", n, "из них aster/open_interest", k)
PY
echo FINAL >> /root/tk512/live/follow.log
