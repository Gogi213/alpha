#!/bin/bash
# М5 цепочка (В-227 пакет F, решение Судьи 12.10): s-v месяца -> busy-replay [--keep keep.csv] -> portfolio-sim cap 0 и 3 -> closes-m5-cap<N>-<мес>.json.
# Тот же вход и те же команды, что дали B1 (tk192-b1d.sh): B1 = qty × 1 (expo меняет только семьи g92..g95), поэтому expo в цепочке B1 не нужен.
#   m5_chain.sh <мес> <рабочий каталог> [keep.csv]      Запуск — юнитом alsched (heavy_guard). Метка <раб>/chain.done, ошибки — <раб>/fail.txt
set -u
MON=${1:?мес}; W=${2:?раб}; KEEP=${3:-}
SV=${M5_SV:-/data/p12r2/s-v}; T=${M5_T:-/data/tk0113/tools}; BR=${M5_BR:-$(dirname "$0")/../compute/busy-replay.py}
DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1; B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
mkdir -p "$W" && cd "$W" || exit 2; rm -rf chain.done fail.txt tree; mkdir tree
K=(); [ -n "$KEEP" ] && K=(--keep "$KEEP")
python3 "$BR" "$SV/$MON" "tree/$MON" --sets $S "${K[@]}" > busy.txt 2>&1 || { echo FAILBUSY >> fail.txt; exit 1; }
for cap in 0 3; do
  python3 "$T/portfolio-sim.py" --epoch "$MON=$W/tree:$MON" --variant "$S@$B=$S/$B" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
    --closes-out "closes-m5-cap$cap-$MON.json" > "psim-m5-cap$cap-$MON.txt" 2> "psim-m5-cap$cap-$MON.log" || echo "FAIL psim $cap" >> fail.txt
done
[ -s fail.txt ] || touch chain.done
