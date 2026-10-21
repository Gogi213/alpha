#!/usr/bin/env bash
# TK-279 П-17 (Г-135/136/137): сутки = НОВЫЕ подходы (ABN3: repeat_count_at_arm, swept) в отдельный журнал + счёт клеток day-cells.sh.
#   tk279-p17b-day.sh <метка> <мес> <сутки>   env: BIN (обязателен), NEWJ (журнал с новыми подходами), SCRIPTS (day-cells.sh),
#   остальное (PLAN P O GT TP JRN ALT_JRN) — для day-cells.sh. Старый журнал /data/p12r3a/jrn не трогается: в новом — ссылки на его файлы,
#   approaches-*.csv — пересчитанные `lob touches --approach-bps 20 --r1-cols --wall-log` (touches-*.csv побайтно равны старым, замер п.1а).
#   Нет колонки repeat_count_at_arm в новых подходах — отказ суток (молча 0 сигналов нельзя, смоук 1017004154793).
set -uo pipefail
lab=$1; MON=$2; d=$3; BIN=${BIN:?BIN обязателен}; NEWJ=${NEWJ:?NEWJ обязателен}; S=${SCRIPTS:-/data/tk0231}
OLD=/data/p12r3a/jrn/$d/wt/sub/D20/$d; H=/data/tk046/$MON/home; JD=$NEWJ/$d/wt/sub/D20/$d; N=$NEWJ/$d/new
if [ ! -e "$NEWJ/$d/done" ]; then
  [ -d "$OLD" ] || { echo "$d НЕТ СТАРОГО ЖУРНАЛА $OLD" >&2; exit 4; }
  RS=$(readlink -f "$H/alpha/epochs/e-$MON/study/root-$d"); AR=/srv/alpha-sda/root/root-$d; [ -d "$AR" ] && RS=$AR
  rm -rf "$NEWJ/$d"; mkdir -p "$JD" "$N"; export HOME=$H
  for f in "$OLD"/*; do ln -s "$f" "$JD/"; done
  : > "$N/fail.txt"
  for s in $(ls "$OLD"/touches-*.csv | sed 's#.*/touches-##; s#\.csv$##'); do
    nice -n 5 "$BIN" lob touches --root "$RS" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out "$N/touches-$s.csv" > "$N/$s.log" 2>&1 || echo "$s TOUCHES_FAIL" >> "$N/fail.txt"
    [ -s "$N/approaches-$s.csv" ] || echo "$s НЕТ approaches" >> "$N/fail.txt"
    head -1 "$N/approaches-$s.csv" 2>/dev/null | grep -q repeat_count_at_arm || echo "$s НЕТ КОЛОНКИ repeat_count_at_arm" >> "$N/fail.txt"
    ln -sf "$N/approaches-$s.csv" "$JD/approaches-$s.csv"
  done
  [ -s "$N/fail.txt" ] && { echo "$d подходы: $(wc -l < "$N/fail.txt") отказов (см. $N/fail.txt)" >&2; exit 3; }
  : > "$NEWJ/$d/done"
fi
exec bash "$S/day-cells.sh" "$lab" "$MON" "$d"
