#!/bin/bash
# Снимок ЦП (CPUUsageNSec юнита) и счётчиков кадров/записей рук D/C1/C2 аудита; строка на руку: метка, время, нс ЦП, кадров реактора, записей.
# usage: audit-cpu.sh <тег> → /data/perf/audit/cpu-<тег>.txt
O=/data/perf/audit/cpu-$1.txt; : > $O
for ex in binance bybit okx bingx; do for arm in D C1 C2; do
  u=audit-$ex-$arm; ns=$(systemctl show -p CPUUsageNSec --value $u)
  r=$(python3 -c "
import json
h=json.load(open('/data/perf/audit/$ex-$arm/health.json'))
print((h.get('reactor') or {}).get('frames',0), (h.get('totals') or {}).get('records',0))" 2>/dev/null || echo "0 0")
  echo "$ex-$arm $(date +%s.%N) $ns $r" >> $O
done; done
