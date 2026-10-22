#!/usr/bin/env bash
# TK-272 (Г-112 q94, Судья 17.10 23:43): пороги Г-112 v10 = v9 + q94 на готовых лентах (W=5,6,8,10,15,60,120,300) -> /data/p12r3a/thr10.
# rc: 14 пороги v10 · 15 v9 не побитно внутри v10
set -uo pipefail
T=/data/tk0272; cd $T
sed 's/^QS = (5, 10, 20, 33, 50, 67, 80, 90, 91, 92, 95)/QS = (5, 10, 20, 33, 50, 67, 80, 90, 91, 92, 94, 95)/' $T/p12-r2b-thresholds-v9.py > $T/p12-r2b-thresholds-v10.py
grep -q '92, 94, 95' $T/p12-r2b-thresholds-v10.py || exit 14
python3 $T/p12-r2b-thresholds-v10.py /data/p12r3a/b1keys-cap0.csv /data/tk0115/delta/w /data/tk064/pool /data/p12r3a/thr10 5=/data/p12r3a/w5 6=/data/p12r3a/w6 8=/data/p12r3a/w8 10=/data/p12r3a/w10 15=/data/p12r3a/w15 60=/data/p12r3a/w60 120=/data/p12r3a/w120 300 || exit 14
python3 - <<'P' > $T/thr10.out 2>&1 || exit 15
import json
a, b = json.load(open("/data/p12r3a/thr9/summary.json"))["summary"], json.load(open("/data/p12r3a/thr10/summary.json"))["summary"]
bm = {r["month"]: r for r in b}
bad = [(r["month"], k) for r in a for k, v in r.items() if bm[r["month"]].get(k) != v]
print("v9 значений", sum(len(r) for r in a), "расхождений", len(bad), bad[:3])
raise SystemExit(1 if bad else 0)
P
echo ok >> $T/thr10.out
