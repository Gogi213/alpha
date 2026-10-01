#!/usr/bin/env bash
# TK-022 ворота «байт в байт» (на Steam Deck): sha256 трёх клеток суток, снятые ДО пересчёта с локальной копии
# (~/alpha/tk022/ref-sha/<D>.sha256), против новых b5/<клетка>/<D>/**. Сверяются сутки с готовым status/<D>.rc.
#   tk022-week-gate.sh <мес> <D>...
set -u
A="$HOME/alpha"; M="$1"; shift
cd "$A/epochs/e-$M/b5" || exit 2
bad=0; ok=0
for d in "$@"; do
  ref="$A/tk022/ref-sha/$d.sha256"
  [ -s "$ref" ] || { echo "$d: эталона нет — пропуск"; continue; }
  grep -q '^rc=0 ' "$A/tk022/status/$d.rc" 2>/dev/null || { echo "$d: счёт не закончен — пропуск"; continue; }
  new=$(for c in p07m-main p07b-base p07a-h2-fr1; do [ -d $c/$d ] && find $c/$d -type f | sort | xargs sha256sum; done)
  if [ "$new" == "$(cat "$ref")" ]; then ok=$((ok+1)); echo "$d: ok ($(echo "$new" | wc -l) файлов)"; else bad=$((bad+1)); echo "$d: РАСХОЖДЕНИЕ"; diff <(echo "$new") "$ref" | head -5; fi
done
echo "ворота: ok $ok, расхождений $bad"
[ $bad -eq 0 ]
