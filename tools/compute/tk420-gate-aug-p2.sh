#!/bin/bash
# TK-420 вар.3–5, B (Gate), август, шаг 2: охват -> пул -> эпизоды -> разметка ног -> детектор (как A, tk420aug/{mkvol,ep,det}.sh).
# Выход: /data/tk0393/tk420aug/gate/{pump-dump-episodes-aug-gate-*.csv,pump-dump-scenarios-legs-aug-gate.csv,det15-*}; итог — gate/p2.rc.
#   alsched submit --kind prod --name tk420-gate-aug-p2 --max-runtime 1h --est 10m --cwd /data/tk0393/tk420aug -- bash /data/tk0393/tk420aug/tk420-gate-aug-p2.sh
cd /data/tk0393/tk420aug || exit 1
rm -f gate/p2.rc; mkdir -p gate/candles; ln -sfn /data/tk0393/tk420aug/candles/gate-fut gate/candles/gate-futures_usdt
trap 'echo $? > gate/p2.rc' EXIT
set -e
P=/data/tk0278/venv/bin/python
python3 - <<'PY'
import csv, gzip, os
d = "candles/gate-fut"
cov, syms = [], []
for fn in sorted(os.listdir(d)):
    s = fn[:-7]; t = []
    with gzip.open(os.path.join(d, fn), "rt") as f:
        next(f)
        for line in f:
            t.append(int(line.split(",", 1)[0]))
    cov.append(["gate", "futures_usdt", s, len(t), 44640, t[0] if t else "", t[-1] if t else "", ""]); syms.append(s)
with open("gate/coverage-aug-gate.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["биржа", "рынок", "инструмент", "минут_с_данными", "окно_мин", "первая_мс", "последняя_мс", "причина_пропусков"]); w.writerows(cov)
vol = list(csv.DictReader(open("/data/tk0393/tk414/det/volatile-coins-sep-2026-10-26.csv", encoding="utf-8")))
cv = {c[2]: c for c in cov}
out, miss = [], []
for s in syms:
    ref = [r for r in vol if r["биржа"] == "gate" and r["рынок"] == "futures_usdt" and r["инструмент"] == s]
    if not ref: miss.append(s); continue
    r = dict(ref[0]); r["дней_с_данными"] = str(cv[s][3] // 1440); r["диапазон_дня_мед_%"] = "999"  # пул фиксирован (не подбор по исходам Aug)
    out.append(r)
w = csv.DictWriter(open("gate/volatile-pool-aug-gate.csv", "w", newline="", encoding="utf-8"), fieldnames=list(vol[0].keys())); w.writeheader(); w.writerows(out)
print(len(out), "miss", miss)
PY
cd gate
[ -s pump-dump-episodes-aug-gate-2026-10-27.csv ] || python3 ../tk413-episodes.py /data/tk0393/tk420aug/gate/candles volatile-pool-aug-gate.csv pump-dump-episodes-aug-gate-2026-10-27.csv 15 > ep.log 2>&1
# разметка ног (pandas есть только локально): tk414-label.py episodes -> pump-dump-scenarios-legs-aug-gate.csv, залито в gate/ вручную (как для A)
test -s pump-dump-scenarios-legs-aug-gate.csv
python3 ../tk414-detector.py /data/tk0393/tk420aug/gate/candles coverage-aug-gate.csv pump-dump-episodes-aug-gate-2026-10-27.csv volatile-pool-aug-gate.csv pump-dump-scenarios-legs-aug-gate.csv det15 15 > det.log 2>&1
