#!/usr/bin/env bash
# TK-609 (BIN и OUT — под сборку): профиль perf тёплого прогона alpha-feat v3 04714685 (flow, wa/S, склад на чтение, 4 потока)
set -uo pipefail
R=/data/p42-tk609; BIN=$R/bin/alpha-feat-v3-04714685; OUT=$R/perf-v3; mkdir -p $OUT
python3 - <<PY
import pandas as pd
e=pd.read_parquet('/data/p42-tk599/ftab/wa/fa95205e801e5/eps-S.parquet', columns=["ep_id","sym","side","sig_ms","turn24h"])
e.to_csv('$OUT/eps.csv', index=False); print(len(e))
PY
export TK594_STORE=/data/p42-tk599/tick-store TK594_STORE_CAP_GB=0 TK594_STORE_MIN_FREE_GB=0
export TK594_TRADES=/data/p42-tk599/wa/aggtrades TK594_METRICS=/data/p42-tk599/metrics-all
echo "[$(date -u +%T)] md5 $(md5sum $BIN | cut -c1-12)"
perf record -F 499 -g -o $OUT/perf.data -- $BIN flow --eps $OUT/eps.csv --out $OUT/out.csv --base-date 2026-06-17 --threads 4 > $OUT/run.out 2> $OUT/run.err
tail -1 $OUT/run.out | cut -c1-400
perf report -i $OUT/perf.data --no-children --sort symbol --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^$" | head -40 > $OUT/report-flat.txt
echo "[$(date -u +%T)] ГОТОВО"; echo done > $OUT/done
