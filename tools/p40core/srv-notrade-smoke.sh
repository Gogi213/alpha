#!/bin/bash
# TK-500: дым ноги без сделок (П-40 §2): Binance DOGEUSDT (A, со сделками) x Bybit DOGEUSDT (B, ob200 без сделок), 17.09; режимы P40_NOTRADE=skip (умолчание) и drain. Числа не интерпретировать.
P=/root/p40core/pq2bin/target/release/pq2bin; B=/dev/shm/p40by
rm -rf $B; mkdir -p $B/TB
$P bybit /root/p40core/data/bybit/DOGEUSDT_2026-09-17_ob200.data.zip $B/B 2>&1 | tail -1
for m in skip drain; do
  P40_NOTRADE=$m nice -n 19 /root/p40core/rs/target/release/p40core /tmp/nt_$m.csv /dev/shm/p40/A $B/B /dev/shm/p40/TA $B/TB 12300 12300 500 2>&1 | grep -E "нога без|готово"
  echo "== $m: исходы exits.csv"
  python3 - /tmp/nt_$m.csv.exits.csv <<'EOF'
import csv, sys, collections
r = list(csv.DictReader(open(sys.argv[1])))
print(len(r), "строк", dict(collections.Counter((x["outcome"], x["exit_kind"]) for x in r)))
EOF
  python3 - /tmp/nt_$m.csv.fills.csv <<'EOF'
import csv, sys
r = list(csv.DictReader(open(sys.argv[1])))
n = len(r)
fb = sum(1 for x in r if float(x["fill_sell_ms"]) >= 0), sum(1 for x in r if float(x["fill_buy_ms"]) >= 0)
print("fills: эпизодов", n, "исполнено продаж", fb[0], "покупок", fb[1])
EOF
done
