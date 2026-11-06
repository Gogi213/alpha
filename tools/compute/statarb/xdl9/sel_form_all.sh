#!/usr/bin/env bash
# TK-493 S1 (П-37 r3.1 §7.2): form для всех каталогов A: реальные пары и нулевые наборы (разбиения 1-3 + контрольное 4) x сдвиги партнёра {+1,+2,-1}
# на панелях ФОРМИРОВАНИЯ. Окно EG = общее пересечение сдвигов 03.10-13.10: сутки 2467-2477, 11 суток (sel_shift.py), from-day 2478.
# alpha=1e9 - не порог отбора, а способ получить статистику ВСЕХ пар; порог t* берётся потом по эмпирическому нулю в единицах баз (sel_fdr.py).
# Бинарь form не менялся с tk486 (60e61cea). Запуск на Deck: systemd-run --user, nice 19, не более 4 потоков (на Deck идут чужие аудиторы).
set -u
BIN=${BIN:-$HOME/xdl9/bin/statarb-tk486-60e61cea}
ROOT=${ROOT:-$HOME/xdl9}
OUT=$ROOT/sel/form_out
THREADS=${THREADS:-4}
mkdir -p "$OUT"
[ "${APPEND_LOG:-0}" = 1 ] || : > "$OUT/form_all.log"
SUBS=${SUBS:-"real null1p1 null1p2 null1m1 null2p1 null2p2 null2m1 null3p1 null3p2 null3m1 null4p1 null4p2 null4m1"}
for d in "$ROOT"/cat/form/*/; do
  id=$(basename "$d")
  mkdir -p "$OUT/$id"
  for sub in $SUBS; do
    [ -d "$d/$sub" ] || continue
    n=$(ls "$d/$sub" | wc -l)
    [ "$n" -ge 4 ] || { echo "$id $sub symbols=$n skip" >> "$OUT/form_all.log"; continue; }
    nice -n 19 $BIN statarb form --panels "$d/$sub" --from-day 2478 --to-day 2479 --window-days 11 --block-chunks 6 \
      --min-obs 300 --alpha 1e9 --threads "$THREADS" --jobs-out "$OUT/$id/$sub.jobs.tmp" --detail-out "$OUT/$id/$sub.csv" \
      >> "$OUT/form_all.log" 2>&1 || echo "$id $sub FAIL rc=$?" >> "$OUT/form_all.log"
    rm -f "$OUT/$id/$sub.jobs.tmp"
  done
done
echo DONE >> "$OUT/form_all.log"
