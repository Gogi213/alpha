#!/usr/bin/env bash
# TK-312: хвост спот-импорта — монето-сутки с rc=1 в import.tsv (PEPE/BONK/SHIB/MOG: размер > 9,2e9 монет не лезет в e9)
# переимпортируются с --qty-shift 3, затем verify только по затронутым (месяц, монета).
set -u
export BIN=${BIN:-/data/tk312/tgt-use/release/alpha} OUT=${OUT:-/srv/alpha-sda/binlog-spot} SRC=${SRC:-/srv/alpha-sda/tk305/spot}
export JOBS=${NX:-1} QTY_SHIFT=3 LOG=$OUT/import-reimport.tsv TMP=/dev/shm/spot-import
S=$(dirname "$0")/spot-import.sh
W=${W:-/data/tk312}
awk -F'\t' '$3==1{print $1, $2}' "$OUT/import.tsv" > "$W/reimport-list.txt"
awk '{print substr($2,1,7), $1}' "$W/reimport-list.txt" | sort -u > "$W/reimport-pairs.txt"
wc -l "$W/reimport-list.txt" "$W/reimport-pairs.txt"
t0=$(date +%s)
bash "$S" import "$W/reimport-list.txt"
t1=$(date +%s)
bash "$S" verify-pairs "$W/reimport-pairs.txt"
t2=$(date +%s)
echo "reimport_s=$((t1-t0)) verify_s=$((t2-t1))" > "$OUT/timing-reimport.txt"
echo done > "$W/reimport.done"
