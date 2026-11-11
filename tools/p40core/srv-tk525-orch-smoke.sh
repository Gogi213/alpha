#!/bin/bash
# TK-525 п.4: сквозной смоук «L1 = сырьё» — Binance DOGEUSDT x Bybit DOGEUSDT, сутки 28.09, K1 (through): orch БЕЗ --l1-trades-raw и С --l1-aux:
# сделки Binance — из .tr.zst L1 (метка .tr.ok), mark/OI/ликвидации — из aux/ L1; parquet CHFT с бокса (chft/...) не качаются (проверка: в raw нет файлов вне p40l1/ и chft/ из aux).
# Запуск на srv: bash srv-tk525-orch-smoke.sh [out]; дерево /root/p40core-tk525o (orch.py ветки tk525-l1-tail), ядро /root/p40core/bin/p40core-nopgo-g87097f2c.
set -e
W=/root/p40core-tk525o; OUT=${1:-/tmp/tk525smoke}; rm -rf $OUT $OUT.pairs.csv $OUT.log
cd $W
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,5,binance,DOGEUSDT,,,,bybit,DOGEUSDT,,,,1,1\n' > $OUT.pairs.csv
P40_L1=1 P40_NOTRADE=through P40_CORE=/root/p40core/bin/p40core-nopgo-g87097f2c \
  python3 orch.py $OUT.pairs.csv $OUT --days 2026-09-28 --chunk 1 --jobs 1 --l1-p 4 --box-conn 16 --l1-aux --validity-l1 2>&1 | tee $OUT.log | tail -5
D=$(ls -d $OUT/1/*)
ls $D
for f in $D/5.aux.csv $D/5.exits.csv $D/5.fills.csv; do [ -f $f ] && echo "$f: $(($(wc -l < $f) - 1)) строк"; done
head -3 $D/5.aux.csv
grep -c "chft/" $OUT/orch.csv 2>/dev/null || true
