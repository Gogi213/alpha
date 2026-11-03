#!/usr/bin/env bash
# TK-486: предел диска стадии (1): чтение gz-свечей с HDD мимо кэша страниц (dd iflag=direct, блок 8 МБ) —
# число не зависит от того, что файлы недавно читали; 1 читатель и 4 читателя. Срезы NR%9==3 и NR%9==4.
set -u
EX="${1:-binance-um}"; SRC=/srv/alpha-sda/candles-1m/$EX
t() { local s=$(date +%s.%N); "$@"; local e=$(date +%s.%N); echo "$(echo "$e - $s" | bc)"; }
for MODE in "3 1" "4 4"; do
  set -- $MODE; R=$1; P=$2
  L=$(cd "$SRC" && ls -p | grep -v / | awk -v r="$R" 'NR%9==r')
  B=$(cd "$SRC" && echo "$L" | xargs stat -L -c %s | awk '{s+=$1} END{print s}')
  S=$(t bash -c "cd $SRC && echo '$L' | xargs -P $P -I{} dd if={} of=/dev/null bs=8M iflag=direct status=none")
  echo "прямое чтение мимо кэша: срез $R, читателей $P, файлов $(echo "$L" | wc -l), байт $B, $S с, $(echo "$B / $S / 1048576" | bc) МБ/с"
done
