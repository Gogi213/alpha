#!/usr/bin/env bash
# G-022 turbo: REST pass AFTER a deferred-REST ladder (`RESTPASS=defer steps.sh`, preferences п. 23): REST check of every window of the given steps, then the full verdict line.
#   [EXCH=okx] [ROOT=/data/okx] [TOOLS=<tools/turbo dir>] restpass.sh <tag> "<steps: N or full ...>" [<windows per step, default 3>]
# Per window: restcheck.py -> rest.json, stepsum.py -> sum.json and one line in <tag>/status-final.txt (`STEP <N> OK|FAIL` per step). Background unit, nothing else on srv meanwhile.
set -u -o pipefail
EXCH="${EXCH:-okx}"; TAG="$1"; STEPS="$2"; NWIN="${3:-3}"
D=${ROOT:-/data/turbo-probe}/$TAG; PY=/root/bnws-venv/bin/python; S=${TOOLS:-/data/turbo-probe/src/tools/turbo}
for NP in $STEPS; do
  ok=1
  for K in $(seq 1 "$NWIN"); do
    L="$TAG-n$NP-w$K"; W="$D/$L"
    [ -s "$W/rest.json" ] || {
      read -r TC NS < "$W/rest-args.txt"; T0=$(cat "$W/t0-ms.txt")
      $PY "$S/restcheck.py" "$EXCH" "$W/data" "$T0" "$(( T0 + 180000 ))" --symbols "$NS" --spread --covered-ms "$TC" --out "$W/rest.json" > "$W/rest.log" 2>&1
    }
    $PY "$S/stepsum.py" "$W" "$L" | tee -a "$D/status-final.txt" || ok=0
  done
  echo "STEP $NP $([ "$ok" = 1 ] && echo OK || echo FAIL)" >> "$D/status-final.txt"
done
echo "ALLDONE" >> "$D/status-final.txt"
