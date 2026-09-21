#!/usr/bin/env bash
# Вердикты по прогонам F10-fix (по мере готовности): для каждого b5/f10fix-D{20,50}/<набор>
# с forms.csv и без вердикта — lob bounce-verdict --log-trials; сводка в study/f10fix-verdicts.txt.
set -uo pipefail
cd /opt/alpha-compute || exit 1
BIN=bin/alpha-cab355c
RUNS=study/runs-2026-09-19.csv
SUM=study/f10fix-verdicts.txt
for D in D20 D50; do
  for dir in b5/f10fix-$D/*/; do
    [ -f "$dir/forms.csv" ] || continue
    set=$(basename "$dir")
    # прогон набора закончен — есть строка done rc=0 после RESTART
    sed -n '/RESTART/,$p' b5/f10fix.log | grep -q "$D $set done rc=0" || continue
    out="study/bounce-verdict-f10fix-$D-$set"
    [ -f "$out.csv" ] && continue
    nice -n 10 $BIN lob bounce-verdict --grid-dir "$dir" --runs-csv $RUNS --out "$out.csv" --log-trials > "$out.log" 2>&1
    rc=$?
    line=$(grep -a "ИТОГ" "$out.log" | tail -1 | sed 's/^bounce-verdict: //')
    echo "$(date -u +%FT%TZ) $D $set rc=$rc :: $line" | tee -a "$SUM"
  done
done
