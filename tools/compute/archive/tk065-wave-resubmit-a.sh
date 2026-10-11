#!/bin/bash
# tk065-wave-resubmit-a.sh: волна A — снять очередные заявки с 2 ГБ и пересдать их (и упавшие сутки) с 4 ГБ; идемпотентно.
B=${1:-r2m-a}; J=/data/sched/jobs; S=/data/sched/alsched.py
while read d M; do
  [ -e /data/tk065/wa-$d.done ] && continue
  act=0
  for f in $(grep -l "\"tk065-wa-$d\"" $J/*.json 2>/dev/null); do
    read st mem < <(python3 -c 'import json,sys;j=json.load(open(sys.argv[1]));print(j["state"],j["mem"])' $f)
    id=$(basename $f .json)
    if [ "$st" = queued ] && [ "$mem" -lt 4 ]; then python3 $S cancel $id; echo "cancel $d $id"
    elif [ "$st" = queued ] || [ "$st" = running ]; then act=1; fi
  done
  if [ $act = 0 ]; then
    GUARD_CELLS_DECLARED=/data/tk065/days/r2a-$d.cells python3 $S submit --cls prod --name tk065-wa-$d --max-runtime 6h --cores 1 --mem 4 -- bash -c "SCR=r2a bash /data/tk065/probe.sh wa-$d $B $M $d" && echo "resubmit $d"
  fi
done < /data/tk065/days/days.tsv
