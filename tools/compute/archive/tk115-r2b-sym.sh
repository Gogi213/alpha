#!/usr/bin/env bash
# TK-115 R2-B: исчерпывающий список упавших символо-суток (GRID_FAIL «wall2 ушли сольным путём», Г-65) на 2026-08-31 и 2026-09-14.
# 1) каждый символ B1 суток — отдельным запуском tk115-r2b-day.sh (B1 = одна строка) → /data/p12r3a/r2b-sym/<сутки>-<SYM>;
# 2) список упавших → /data/p12r3a/r2b-failed-symdays.csv; 3) B1 урезан ровно на них, обе сутки пересчитаны в основной выход r2b.
set -uo pipefail
B1=/data/tk0115/delta/b1-symdays.csv; S=/data/p12r3a/r2b-sym; L=/data/p12r3a/r2b-failed-symdays.csv
mkdir -p "$S/b1"; rm -f "$S/b1"/*
one() { d=$1; sym=$2; mon=$3; x="$S/b1/$d-$sym.csv"
  awk -F, -v d="$d" -v s="$sym" 'NR==1||($2==d&&$3==s)' "$B1" > "$x"
  B1=$x O=$S GT=2 bash /data/tk0115/tk115-r2b-day.sh "$d-$sym" "$mon" "$d"; }
export -f one; export B1 S
awk -F, 'NR>1&&($2=="2026-08-31"||$2=="2026-09-14"){print $2, $3, ($2=="2026-08-31"?"aug":"sep")}' "$B1" | xargs -P4 -L1 bash -c 'one "$@"' _
echo "day,symbol" > "$L"
for f in "$S"/*/fail.txt; do [ -s "$f" ] || continue; n=$(basename "$(dirname "$f")"); echo "${n:0:10},${n:11}" >> "$L"; done
echo "упавших: $(($(wc -l < "$L")-1))"; cat "$L"
B1X=/data/tk0115/delta/b1-symdays-r2b-tail2.csv
awk -F, 'NR==FNR{if(FNR>1)k[$1","$2]=1;next} !(($2","$3) in k)' "$L" "$B1" > "$B1X"
for d in 2026-08-31 2026-09-14; do rm -rf /data/p12r3a/r2b/$d/claim /data/p12r3a/r2b/$d/done /data/p12r3a/r2b/$d/out /data/p12r3a/r2b/$d/w-$d; done
B1=$B1X bash /data/tk0115/tk115-r2b-day.sh 2026-08-31 aug 2026-08-31
B1=$B1X bash /data/tk0115/tk115-r2b-day.sh 2026-09-14 sep 2026-09-14
cat /data/p12r3a/r2b/2026-08-31/fail.txt /data/p12r3a/r2b/2026-09-14/fail.txt
echo "rc итог: пусто = ок"
