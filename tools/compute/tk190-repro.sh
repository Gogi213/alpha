#!/usr/bin/env bash
# TK-190: воспроизведение Г-65 «wall2 ушли сольным путём» на упавших символо-сутках R2-B с диагностикой TK190_DIAG
# (сборка alpha-tk190-r2; одна строка B1 на символо-сутки, как в tk115-r2b-sym.sh) → ${OUTD:-/data/tk0190/repro}/<сутки>-<SYM>.
set -uo pipefail
S=/data/p12r3a/r2b-sym/b1
OUTD=${OUTD:-/data/tk0190/repro}
for x in "2026-08-31 ADAUSDT aug" "2026-08-31 GRAMUSDT aug" "2026-09-14 STXUSDT sep"; do set -- $x
  rm -rf "${OUTD:-/data/tk0190/repro}/$1-$2"
  BIN=${BIN:-/data/tk0190/alpha-tk190-r2} B1="$S/$1-$2.csv" O=$OUTD GT=2 bash /data/tk0115/tk115-r2b-day.sh "$1-$2" "$3" "$1"
  grep -h '^TK190\|^Error' "${OUTD:-/data/tk0190/repro}/$1-$2/log/$1.grid.log" | sort | uniq -c | sort -rn | head -40 > "${OUTD:-/data/tk0190/repro}/$1-$2.diag"
done
touch ${OUTD:-/data/tk0190/repro}/done
