#!/usr/bin/env bash
# G-022 turbo: re-evaluate the windows of a FINISHED BingX ladder with the final tools (abwin + restcheck + stepsum), offline: no collector, no REST (the snapshots are in the window directories).
#   [ROOT=/data/bingx] [TOOLS=<tools/turbo dir>] reeval.sh <tag> "<steps: N or full ...>" [<windows per step, default 3>]
# Per window: abwin.py (late_after_covered), restcheck.py bingx --snap, stepsum.py -> one line in <tag>/status-final.txt (`STEP <N> OK|FAIL` per step). Needed when the checker was fixed after some windows ran
# (the window data and the REST snapshot do not change; only the verdict code does). Run when nothing of the exchange collects on srv.
set -u -o pipefail
TAG="$1"; STEPS="$2"; NWIN="${3:-3}"
D=${ROOT:-/data/bingx}/$TAG; PY=/root/bnws-venv/bin/python; S=${TOOLS:-/data/bingx/src/tools/turbo}
: > "$D/status-final.txt"
for NP in $STEPS; do
  ok=1
  for K in $(seq 1 "$NWIN"); do
    L="$TAG-n$NP-w$K"; W="$D/$L"
    T0=$(cat "$W/t0-ms.txt"); T1=$(( T0 + 180000 ))
    TC=$($PY -c "import json; c=json.load(open('$W/cov.json')); m=c['first_subscribed_s']['max']; print($T0 + int(((m if m is not None else 180) + 3) * 1000))")
    $PY "$S/abwin.py" --run "X=$W/data" --t0 "$T0" --t1 "$T1" --pool "$D/pool-$NP.json" --covered-ms "$TC" --out "$W/win.json" > "$W/win.log" 2>&1
    $PY "$S/restcheck.py" bingx "$W/data" "$T0" "$T1" --snap "$W/rest-snap.json.gz" --covered-ms "$TC" --out "$W/rest.json" > "$W/rest.log" 2>&1
    $PY "$S/stepsum.py" "$W" "$L" | tee -a "$D/status-final.txt" || ok=0
  done
  echo "STEP $NP $([ "$ok" = 1 ] && echo OK || echo FAIL)" >> "$D/status-final.txt"
done
echo "ALLDONE" >> "$D/status-final.txt"
