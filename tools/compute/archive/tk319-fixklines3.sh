#!/usr/bin/env bash
# TK-319: добор гейта «ненулевые сигналы по каждому месяцу» (fixklines2: март и сентябрь events=0 — у 5 монет дня не было ни одного
# касания, прошедшего фильтр S0; events=0 = сутки без сигнала, книга не читается). Здесь: S0 на NC монетах (по размеру файла касаний)
# дня с самым глубоким провалом BTC в мартe и сентябре. Результат — $K/fixklines/RESULT3.txt, метка $K/fixklines/done3.
set -uo pipefail
K=/data/tk0319; B=$K/bin; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; F=$K/fixklines; SP=$F/splan
NC=${NC:-40}; R=$F/RESULT3.txt; : > "$R"; rm -rf "$F/s0b"
say() { echo "$*" | tee -a "$R"; }
sum() { awk '{s+=$1} END{print s+0}'; }
for m in ${MONTHS:-mar sep}; do
  d=$(awk -F, -v m="$m" 'NR>1&&$1==m{print $2}' "$F/s0-days.csv" | head -1)
  printf 'month,day,symbol\n' > "$F/b1b-$d.csv"
  for s in $(ls -S "$K/jrn/$d/wt/sub/D20/$d"/touches-*.csv | head -$((NC * 2))); do
    b=${s##*/touches-}; b=${b%.csv}; grep -q ",$d,$b\$" "$K/b1-symdays.csv" && echo "$m,$d,$b" >> "$F/b1b-$d.csv"
  done
  awk 'NR==1||c++<'"$NC" "$F/b1b-$d.csv" > "$F/b1b-$d.tmp"; mv "$F/b1b-$d.tmp" "$F/b1b-$d.csv"
  PLAN=spots0 P=$SP O=$F/s0b EPOCH=$K/e-$m VERDICT=$K/verdict.csv B1=$F/b1b-$d.csv JRN=$K/jrn RG=$K/regime BIN=$BIN GT=1 TP=2 bash "$B/day-cells.sh" "$m-$d" $m $d > /dev/null 2>&1
  L=$(cat "$F/s0b/$m-$d/log/"*.log 2>/dev/null | grep -E ' touches=.*events=')
  ev=$(echo "$L" | grep -o 'events=[0-9]*' | cut -d= -f2 | sum); rd=$(echo "$L" | grep -o 'rounds=[0-9]*' | cut -d= -f2 | sum)
  nsig=$(echo "$L" | grep -vc 'events=0 ')
  say "S0 $m $d: монет $(($(wc -l < "$F/b1b-$d.csv") - 1)), с сигналом (events>0) $nsig, events=$ev, rounds=$rd, fail=$(wc -c < "$F/s0b/$m-$d/fail.txt" 2>/dev/null)"
done
touch "$F/done3"
