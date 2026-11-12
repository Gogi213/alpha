#!/usr/bin/env bash
# TK-402 шаг 2 с пределом возраста котировки (CEO 21:28): tk402-step2.py run, A = 1/5/30 с за один проход по монете; npz -> /data/tk0402/s2a{1000,5000,30000}/<COIN>.npz
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk402-step2-age --cores 15 --mem 24 --max-runtime 6h --cwd /data/tk0402 -- bash /data/tk0402/tk402-runA.sh
# Метка /data/tk0402/step2a.done только если по всем монетам есть все три npz, иначе step2a.failed.
set -uo pipefail
W=/data/tk0402; PY=/data/tk0380/npvenv/bin/python; DAYS=2026-10-15,2026-10-16,2026-10-17,2026-10-18,2026-10-19,2026-10-20,2026-10-21,2026-10-22
if [ "${1:-}" = one ]; then
  c=$2; [ -s $W/s2a30000/$c.npz ] && [ -s $W/s2a5000/$c.npz ] && [ -s $W/s2a1000/$c.npz ] && exit 0
  $PY $W/tk402-step2.py run "$c" $DAYS "$W/s2a%d/$c.npz" >> $W/s2a.log.d/$c.log 2>&1
  exit $?
fi
mkdir -p $W/s2a1000 $W/s2a5000 $W/s2a30000 $W/s2a.log.d; rm -f $W/step2a.done $W/step2a.failed
cp $W/s2/coins.txt $W/s2a.coins.txt
echo "монет $(wc -l < $W/s2a.coins.txt) старт $(date +%T)" > $W/step2a.log
xargs -a $W/s2a.coins.txt -P "${P:-15}" -L 1 bash $W/tk402-runA.sh one; rc=$?
n=$(ls $W/s2a1000/*.npz $W/s2a5000/*.npz $W/s2a30000/*.npz 2>/dev/null | wc -l); echo "rc=$rc npz=$n конец $(date +%T)" >> $W/step2a.log
if [ "$rc" = 0 ] && [ "$n" = "$((3 * $(wc -l < $W/s2a.coins.txt)))" ]; then echo '{"done":1}' > $W/step2a.done; else touch $W/step2a.failed; fi
