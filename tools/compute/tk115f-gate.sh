#!/usr/bin/env bash
# TK-115 гейт «флаг выкл. = прежний бинарник»: новый alpha-tk115f (формы tape<Q>/cxl<Q>, в сетке не заданы) на одних сутках
# через tk115-delta-day.sh против готового выхода волны (бинарник b26tk115r2): signals/rounds и e106 побайтно (md5 по файлам).
#   tk115f-gate.sh <сутки> [мес]   → /data/tk0115/delta/gate-f/<сутки>/, маркер /data/tk0115/delta/gate-f/<сутки>.done (rc внутри)
set -uo pipefail
d=${1:-2026-03-07}; mon=${2:-mar}
G=/data/tk0115/delta/gate-f; mkdir -p "$G"; rm -rf "$G/$d" "$G/$d.done"
BIN=/data/tk0115/bin/alpha-tk115f TP=4 bash /data/tk0115/tk115-delta-day.sh "gate-f/$d" "$mon" "$d"
A=/data/tk0115/delta/wave/$d; B=/data/tk0115/delta/gate-f/$d
rc=0; n=0
for sub in signals e106 walls; do
  while IFS= read -r f; do
    n=$((n+1))
    cmp -s "$A/$sub/$f" "$B/$sub/$f" || { echo "DIFF $sub/$f"; rc=1; }
  done < <(cd "$A/$sub" && find . -type f | sort)
  [ "$(cd "$A/$sub" && find . -type f | wc -l)" = "$(cd "$B/$sub" && find . -type f | wc -l)" ] || { echo "COUNT $sub"; rc=1; }
done
cat "$B/fail.txt" 2>/dev/null
echo "files=$n rc=$rc"
echo "$rc" > "$G/$d.done"
