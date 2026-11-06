#!/usr/bin/env bash
# TK-493 S2 (П-37 r3.2 §7.2, D13): form для всех каталогов A: реальные пары (real) и нуль на РЁБРАХ реального графа - по запуску на неупорядоченную пару
# смещений серий {p2, p1, r, m1} (sel_shift.RUNS: al_p2_p1, al_p2_r, al_p2_m1, al_p1_r, al_p1_m1, al_r_m1; в каталоге `S` и `S@B` - символ с первым и вторым
# смещением) на панелях ФОРМИРОВАНИЯ. Окно EG = общее пересечение сдвигов 03.10-13.10: сутки 2467-2477, 11 суток (sel_shift.py), from-day 2478.
# alpha=1e9 - не порог отбора, а способ получить статистику ВСЕХ пар; порог t* берётся потом по эмпирическому нулю в единицах баз (sel_fdr.py).
# Бинарь form не менялся с tk486 (60e61cea). Запуск на Deck: systemd-run --user; потоки - THREADS (по умолчанию 8: Deck - узел счёта, владелец 06.11).
set -u
BIN=${BIN:-$HOME/xdl9/bin/statarb-tk486-60e61cea}
ROOT=${ROOT:-$HOME/xdl9}
OUT=$ROOT/sel/form_out
THREADS=${THREADS:-8}
mkdir -p "$OUT"
[ "${APPEND_LOG:-0}" = 1 ] || : > "$OUT/form_all.log"
SUBS=${SUBS:-"real al_p2_p1 al_p2_r al_p2_m1 al_p1_r al_p1_m1 al_r_m1"}
for d in "$ROOT"/cat/form/*/; do
  id=$(basename "$d")
  mkdir -p "$OUT/$id"
  for sub in $SUBS; do
    [ -d "$d/$sub" ] || continue
    n=$(ls "$d/$sub" | wc -l)
    [ "$sub" = real ] || n=$((n / 2))               # в al_* по две панели на символ
    [ "$n" -ge 4 ] || { echo "$id $sub symbols=$n skip" >> "$OUT/form_all.log"; continue; }
    $BIN statarb form --panels "$d/$sub" --from-day 2478 --to-day 2479 --window-days 11 --block-chunks 6 \
      --min-obs 300 --alpha 1e9 --threads "$THREADS" --jobs-out "$OUT/$id/$sub.jobs.tmp" --detail-out "$OUT/$id/$sub.csv" \
      >> "$OUT/form_all.log" 2>&1 || echo "$id $sub FAIL rc=$?" >> "$OUT/form_all.log"
    rm -f "$OUT/$id/$sub.jobs.tmp"
  done
done
echo DONE >> "$OUT/form_all.log"
