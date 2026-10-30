#!/bin/bash
# TK-463: добавка к bench3 — ТФ 240: 449 ячеек, 2000 первых событий TWT, 15 потоков, без gz. Итог в /data/tk0463/bench3/bench4.log
set -u
B=/data/tk0463; OUT=$B/bench3; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund
$PY - <<'PYEOF'
import pandas as pd
ev = pd.read_csv("/data/tk0461/ev/TWTUSDT-240.csv.gz").head(2000)[["ev", "dir", "side", "t0", "tt0", "atr_bps"]].copy()
ev["ss_bps"] = 1.7 * ev.atr_bps
ev["ot1_bps"] = 0.9 * ev.atr_bps
ev.to_csv("/data/tk0463/bench3/ev-240.csv", index=False)
PYEOF
r=$( { /usr/bin/time -f "стена %e с" $B/bin/tk463-sim $SRC/TWTUSDT.csv.gz 240 $OUT/ev-240.csv $FUND $OUT/cells449.csv $OUT/d2b-240.tmp --threads 15 2>&1 | grep -E "счёт|строк|стена" | tr '\n' ' '; } 2>&1)
echo "D2b 449 ячеек TWT-240, 2000 событий, 15 потоков: $r" >$OUT/bench4.log
rm -f $OUT/*.tmp $OUT/ev-240.csv
