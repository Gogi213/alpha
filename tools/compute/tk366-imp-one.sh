#!/usr/bin/env bash
# TK-366 (G-016), фаза 2: одна монето-сутки из уже скачанного сырья (tk366-dl.sh) → бинлог v3/v4; после успеха сырьё удаляется, при отказе остаётся.
#   tk366-imp-one.sh fut|spot SYM DAY RAWDIR    env: BIN INSTR  — журнал $D/log/import2.tsv (поля как у tk366-one.sh)
mk=$1; sym=$2; d=$3; rawd=$4; mon=${d:0:7}; D=${TKD:-/srv/alpha-sda/tk366}
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; INSTR=${INSTR:-$D/instruments.csv}; LOG=$D/log/import2.tsv
out=$D/bin-$mk/e-$mon; TMP=$D/tmp/i; mkdir -p "$out" "$TMP"
b="$out/$sym-$d.binlog"; ob=$rawd/$mk-$sym-$d.zip; tr=$rawd/$mk-$sym-$d.csv.gz; tb="$TMP/$mk-$sym-$d.ob"; tt="$TMP/$mk-$sym-$d.csv"
log() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mk" "$sym" "$d" "$1" "${2:-0}" "${3:-0}" 0 "${4:-0}" "${5:-0}" "${6:-}" >> "$LOG"; }
[ -f "$b" ] && { log skip; rm -f "$ob" "$tr"; exit 0; }
[ -f "$ob" ] && [ -f "$tr" ] || { log no-raw; exit 0; }
if [ "$mk" = fut ]; then extra=(--instruments "$INSTR"); else extra=(--spot); fi
t1=$(date +%s); rc=0
unzip -p "$ob" > "$tb" && gunzip -c "$tr" > "$tt" &&
  msg=$(nice -n 10 "$BIN" lob import-archive --symbol "$sym" --day "$d" --ob "$tb" --trades "$tt" --root "$out" --steps-from-day "${extra[@]}" 2>&1)
rc=$?
[ $rc -ne 0 ] && rm -f "$b" "$b.part"
log "$([ $rc = 0 ] && echo ok || echo fail:$rc)" "$(stat -c%s "$ob")" "$(stat -c%s "$tr")" $(( $(date +%s)-t1 )) "$(stat -c%s "$b" 2>/dev/null || echo 0)" "$(echo "$msg" | tr '\n\t' '  ' | cut -c1-300)"
rm -f "$tb" "$tt"; [ $rc = 0 ] && rm -f "$ob" "$tr"
