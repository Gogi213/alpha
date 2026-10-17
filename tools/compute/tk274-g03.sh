#!/usr/bin/env bash
# TK-274 Г-03: B1 × 5 seed на двойниках, все B1-сутки янв–сен (251). Сначала одни лёгкие сутки (дым): fail/cmp DIFF → стоп rc 6.
#   tk274-g03.sh   env: BIN NX=3 TP=4 GT=2   результат /data/tk0274/g03/out/<метка>/ ; rc → /data/tk0274/g03.rc
set -uo pipefail
R=/data/tk0274/g03; S=$(cd "$(dirname "$0")" && pwd)
export BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk274-pgo} TP=${TP:-4} GT=${GT:-2} P=$R/plan O=$R/out
mkdir -p "$O"; D=$R/days.txt
smoke=$(tail -n1 "$D"); set -- $smoke
bash "$S/tk274-g03-day.sh" "run-$1-$2" "$1" "$2"
f=$O/run-$1-$2; [ -e "$f/done" ] && [ ! -s "$f/fail.txt" ] || { echo "дым $1 $2: fail.txt/нет done" >&2; exit 6; }
grep -q DIFF "$f/cmp.txt" && { echo "дым $1 $2: approaches не совпали с кэшем TK-115" >&2; exit 6; }
xargs -P "${NX:-3}" -L1 bash -c 'bash "$0/tk274-g03-day.sh" "run-$1-$2" "$1" "$2"' "$S" < "$D"
rc=$?
n=$(cat "$O"/*/fail.txt 2>/dev/null | wc -l); [ "$n" = 0 ] || { echo "fail.txt: $n строк" >&2; rc=3; }
exit $rc
