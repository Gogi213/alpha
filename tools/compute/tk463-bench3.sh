#!/bin/bash
# TK-463: (1) полный D2a ядром: все пары монета x ТФ из /data/tk0461/ev, 28 ячеек, 15 процессов x 1 поток, gz-1, свои выводы в /data/tk0463/d2a_full (стена, сумма строк);
# (2) масштаб D2b: 449 ячеек (набор из 12 ячеек гейта D2b по кругу) на 2000 первых событий TWT-60 и TWT-1440, 15 потоков, без gz — скорость строк/с.
# Итог: /data/tk0463/bench3/BENCH3.DONE и bench3.log.
set -u
B=/data/tk0463; OUT=$B/bench3; DF=$B/d2a_full; BIN=$B/bin/tk463-sim; SRC=/srv/alpha-sda/candles-1m/binance-um; W=/data/tk0461; FUND=/data/tk0455/fund; PY=/data/tk421/venv/bin/python
mkdir -p $OUT $DF; rm -f $OUT/BENCH3.DONE $OUT/bench3.log
source "$(dirname "$0")/tk463-lib.sh"; pairs_make $W/ev $OUT/pairs.txt || exit 2   # TK-464: без служебных файлов ev (иначе xargs -L1 склеивает строки)
echo "пар $(wc -l <$OUT/pairs.txt)" >>$OUT/bench3.log
job() { c=$1; tf=$2; $BIN $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $FUND $B/out/cells.csv $DF/$c-$tf.csv.gz --threads 1 --gz 1 2>&1 | grep -E "строк" | sed "s|^|$c $tf |"; }
export -f job; export B DF BIN SRC W FUND
s=$(date +%s.%N)
xargs -P 15 -L1 bash -c 'job $0 $1' < $OUT/pairs.txt >$OUT/d2a.rows
e=$(date +%s.%N)
echo "D2a полный: стена $(echo "$e - $s" | bc) с; строк $(awk '{s+=$(NF-1)} END{print s}' $OUT/d2a.rows)" >>$OUT/bench3.log
$PY - <<'PYEOF'
import pandas as pd
c = pd.read_csv("/data/tk0463/d2b/cells.csv", keep_default_na=True)
n = 449
pd.concat([c] * (n // len(c) + 1)).head(n).to_csv("/data/tk0463/bench3/cells449.csv", index=False)
for tf in (60, 1440):
    ev = pd.read_csv(f"/data/tk0461/ev/TWTUSDT-{tf}.csv.gz").head(2000)[["ev", "dir", "side", "t0", "tt0", "atr_bps"]].copy()
    ev["ss_bps"] = 1.7 * ev.atr_bps
    ev["ot1_bps"] = 0.9 * ev.atr_bps
    ev.to_csv(f"/data/tk0463/bench3/ev-{tf}.csv", index=False)
PYEOF
for tf in 60 1440; do
  r=$( { /usr/bin/time -f "стена %e с" $BIN $SRC/TWTUSDT.csv.gz $tf $OUT/ev-$tf.csv $FUND $OUT/cells449.csv $OUT/d2b-$tf.tmp --threads 15 2>&1 | grep -E "счёт|строк|стена" | tr '\n' ' '; } 2>&1)
  echo "D2b 449 ячеек TWT-$tf, 2000 событий, 15 потоков: $r" >>$OUT/bench3.log; done
rm -f $OUT/*.tmp $OUT/ev-*.csv
echo done >$OUT/BENCH3.DONE
