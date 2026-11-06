#!/usr/bin/env bash
# TK-493 F7: form для всех каталогов A (реальные пары и 4 разбиения нуля) на панелях ФОРМИРОВАНИЯ 02.10-14.10 (13 суток).
# alpha=1e9 - не порог отбора, а способ получить статистику ВСЕХ пар (select_pairs пропускает всё, что считается):
# порог t* берётся потом по эмпирическому нулю (sel_fdr.py), а не по Холму form.
# Бинарь form не менялся с tk486 (60e61cea). Запуск на Deck: systemd-run --user.
set -u
BIN=${BIN:-$HOME/xdl9/bin/statarb-tk486-60e61cea}
ROOT=${ROOT:-$HOME/xdl9}
OUT=$ROOT/sel/form_out
mkdir -p "$OUT"
[ "${APPEND_LOG:-0}" = 1 ] || : > "$OUT/form_all.log"
# SUBS - какие подкаталоги гнать (по умолчанию все); SKIP_CRYPTO=1 - пропустить каталоги крипты (нуль крипты не менялся)
SUBS=${SUBS:-"real null1 null2 null3 null4"}
for d in "$ROOT"/cat/form/*/; do
  id=$(basename "$d")
  [ "${SKIP_CRYPTO:-0}" = 1 ] && case "$id" in *-crypto) continue;; esac
  mkdir -p "$OUT/$id"
  for sub in $SUBS; do
    [ -d "$d/$sub" ] || continue
    n=$(ls "$d/$sub" | wc -l)
    [ "$n" -ge 4 ] || { echo "$id $sub symbols=$n skip" >> "$OUT/form_all.log"; continue; }
    $BIN statarb form --panels "$d/$sub" --from-day 2479 --to-day 2480 --window-days 13 --block-chunks 6 \
      --min-obs 300 --alpha 1e9 --threads 8 --jobs-out "$OUT/$id/$sub.jobs.tmp" --detail-out "$OUT/$id/$sub.csv" \
      >> "$OUT/form_all.log" 2>&1 || echo "$id $sub FAIL rc=$?" >> "$OUT/form_all.log"
    rm -f "$OUT/$id/$sub.jobs.tmp"
  done
done
echo DONE >> "$OUT/form_all.log"
