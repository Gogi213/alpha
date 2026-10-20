#!/usr/bin/env bash
# TK-272 (Г-10 q9 / окно 9 ч, Г-112 W=8 и q92): режим 9h на 9 мес. -> /data/tk0272/regime9h/<мес>; пороги BTC v3 (+q9) из минутных свечей;
# лента W=8 (--tape-log 8, те же сделки B1, что у W=6, PGO tk272) -> /data/p12r3a/w8; пороги Г-112 v9 (+W8, +q92) -> /data/p12r3a/thr9.
# rc: 11 режим · 12 пороги BTC · 13 v2 не побитно · 3 лента W=8 · 14 пороги v9 · 15 v8 не побитно внутри v9
set -uo pipefail
T=/data/tk0272; cd $T
for m in jan feb mar apr may jun jul aug sep; do python3 $T/r9/tk272-regime-9h.py /data/tk0196/regime/$m $T/regime9h/$m || exit 11; done
sed 's/^QUANTILES = (5, 6, 8, 10,/QUANTILES = (5, 6, 8, 9, 10,/' $T/btc-thresholds.py > $T/btc-thresholds-v3.py
refs=(); for m in jan feb mar apr may jun jul aug sep; do refs+=(--ref /data/tk0196/regime/$m/ref-BTCUSDT-1m.csv); done
python3 $T/btc-thresholds-v3.py "${refs[@]}" --from 2026-01 --out $T/btc-thresholds-v3.json || exit 12
python3 - <<'P' || exit 13
import json
a, b = json.load(open("/data/tk0272/btc-thresholds-v2.json")), json.load(open("/data/tk0272/btc-thresholds-v3.json"))
bad = [(m, n, q) for m, per in a.items() for n, qs in per.items() for q, t in qs.items() if b.get(m, {}).get(n, {}).get(q) != t]
print("v2 ключей", sum(len(qs) for per in a.values() for qs in per.values()), "расхождений", len(bad), bad[:3])
raise SystemExit(1 if bad else 0)
P
export TL=8 D=${D:-2} GT=${GT:-2} BIN=/opt/alpha-compute/bin/alpha-tk272-pgo
bash /data/tk0115/tk120-thr-w-wave.sh 8
n=$(cat /data/p12r3a/w8/*/fail.txt 2>/dev/null | wc -l); d=$(ls /data/p12r3a/w8/*/done 2>/dev/null | wc -l)
echo "w8: done=$d fail=$n" > $T/thr9.out
[ "$n" = 0 ] && [ "$d" = 251 ] || exit 3
sed 's/^QS = (5, 10, 20, 33, 50, 67, 80, 90, 91, 95)/QS = (5, 10, 20, 33, 50, 67, 80, 90, 91, 92, 95)/' $T/p12-r2b-thresholds-v8.py > $T/p12-r2b-thresholds-v9.py
python3 $T/p12-r2b-thresholds-v9.py /data/p12r3a/b1keys-cap0.csv /data/tk0115/delta/w /data/tk064/pool /data/p12r3a/thr9 5=/data/p12r3a/w5 6=/data/p12r3a/w6 8=/data/p12r3a/w8 10=/data/p12r3a/w10 15=/data/p12r3a/w15 60=/data/p12r3a/w60 120=/data/p12r3a/w120 300=/data/p12r3a/w300 >> $T/thr9.out 2>&1 || exit 14
python3 - <<'P' >> $T/thr9.out 2>&1 || exit 15
import json
a, b = json.load(open("/data/p12r3a/thr8/summary.json"))["summary"], json.load(open("/data/p12r3a/thr9/summary.json"))["summary"]
bm = {r["month"]: r for r in b}
bad = [(r["month"], k) for r in a for k, v in r.items() if bm[r["month"]].get(k) != v]
print("v8 значений", sum(len(r) for r in a), "расхождений", len(bad), bad[:3])
raise SystemExit(1 if bad else 0)
P
echo ok >> $T/thr9.out
