#!/bin/bash
# TK-037: монето-сутки со сменой шага внутри суток — импорт --steps-from-day из сохранённого сырья (нужен файл, не pipe).
#   tk037-fix.sh SYM DAY   env: RAW ROOT BIN INSTR LOG TMPD
sym=$1; d=$2; mon=${d:0:7}
RAW=${RAW:-/data/tk037raw}; RAW2=${RAW2:-/root/tk037raw}; ROOT=${ROOT:-/data/tk037/roots}; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk037-day}
INSTR=${INSTR:-/data/tk037/instruments.csv}; LOG=${LOG:-/data/tk037/fix.log}; TMPD=${TMPD:-/data/tk037/tmpob}
out=$ROOT/e-$mon; mkdir -p "$out" "$TMPD"
[ -f "$out/$sym-$d.binlog" ] && { echo "$sym $d skip-have" >> "$LOG"; exit 0; }
ob=$RAW/ob-$sym-$d.zip; tr=$RAW/tr-$sym-$d.csv.gz; raw=$TMPD/$sym-$d.data
[ -s "$ob" ] || { ob=$RAW2/ob-$sym-$d.zip; tr=$RAW2/tr-$sym-$d.csv.gz; }
[ -s "$ob" ] && [ -s "$tr" ] || { echo "$sym $d no-raw" >> "$LOG"; exit 0; }
unzip -p "$ob" > "$raw"
$BIN lob import-archive --symbol $sym --day $d --ob "$raw" --trades <(gunzip -c "$tr") --instruments "$INSTR" --root "$out" --steps-from-day > "$raw.out" 2>&1
rc=$?
[ $rc -ne 0 ] && rm -f "$out/$sym-$d.binlog" "$out/$sym-$d.binlog.part"
echo "$sym $d rc=$rc $(grep 'шаг суток' "$raw.out" | sed 's/.*— //' | cut -c1-200) | $(tail -1 "$raw.out" | cut -c1-160)" >> "$LOG"
rm -f "$raw" "$raw.out"
