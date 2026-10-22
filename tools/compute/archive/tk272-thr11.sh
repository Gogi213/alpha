#!/usr/bin/env bash
# TK-272 (Г-10, окна 10/11 ч, Судья 17.10 23:43): режим с колонками 9/10/11 ч на 9 мес. -> /data/tk0272/regime11h/<мес> (дописка недостающих
# к regime9h); пороги BTC v4 (окна +10, +11 ч) из минутных свечей, v3 побитно внутри v4.
# rc: 11 режим · 12 пороги BTC · 13 v3 не побитно
set -uo pipefail
T=/data/tk0272; cd $T; mkdir -p $T/r11
cp -f $T/r11src/regime.py $T/r11src/tk272-regime-9h.py $T/r11/
for m in jan feb mar apr may jun jul aug sep; do python3 $T/r11/tk272-regime-9h.py $T/regime9h/$m $T/regime11h/$m || exit 11; done
sed 's/^WINDOWS_H = (1, 2, 3, 4, 6, 8, 9, 12, 24)/WINDOWS_H = (1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 24)/' $T/btc-thresholds-v3.py > $T/btc-thresholds-v4.py
grep -q '9, 10, 11, 12' $T/btc-thresholds-v4.py || exit 12
refs=(); for m in jan feb mar apr may jun jul aug sep; do refs+=(--ref /data/tk0196/regime/$m/ref-BTCUSDT-1m.csv); done
python3 $T/btc-thresholds-v4.py "${refs[@]}" --from 2026-01 --out $T/btc-thresholds-v4.json || exit 12
python3 - <<'P' > $T/thr11.out 2>&1 || exit 13
import json
a, b = json.load(open("/data/tk0272/btc-thresholds-v3.json")), json.load(open("/data/tk0272/btc-thresholds-v4.json"))
bad = [(m, n, q) for m, per in a.items() for n, qs in per.items() for q, t in qs.items() if b.get(m, {}).get(n, {}).get(q) != t]
print("v3 ключей", sum(len(qs) for per in a.values() for qs in per.values()), "расхождений", len(bad), bad[:3])
raise SystemExit(1 if bad else 0)
P
echo ok >> $T/thr11.out
