#!/usr/bin/env bash
# TK-402 FS (Gate-фьюч x Bybit-спот) шаг 2 с пределом возраста котировки (CEO 21:28): tk402-step2.py run, A = 1/5/30 с за один проход по монете; npz -> /data/tk0402/s2s{1000,5000,30000}/<COIN>.npz
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk402-step2-fs --cores 15 --mem 24 --max-runtime 6h --cwd /data/tk0402 -- bash /data/tk0402/tk402-runS.sh
# Метка /data/tk0402/step2s.done только если по всем монетам есть все три npz, иначе step2s.failed.
set -uo pipefail
export TK402_MK=spot
W=/data/tk0402; PY=/data/tk0380/npvenv/bin/python; DAYS=2026-10-15,2026-10-16,2026-10-17,2026-10-18,2026-10-19,2026-10-20,2026-10-21,2026-10-22
if [ "${1:-}" = one ]; then
  c=$2; [ -s $W/s2s30000/$c.npz ] && [ -s $W/s2s5000/$c.npz ] && [ -s $W/s2s1000/$c.npz ] && exit 0
  $PY $W/tk402-step2.py run "$c" $DAYS "$W/s2s%d/$c.npz" >> $W/s2s.log.d/$c.log 2>&1
  exit $?
fi
mkdir -p $W/s2s1000 $W/s2s5000 $W/s2s30000 $W/s2s.log.d; rm -f $W/step2s.done $W/step2s.failed
awk -F'	' 'NR>1 && $3!="" {print $1}' $W/stage1.tsv | grep -Fxf $W/s2a.coins.txt > $W/s2s.coins.txt
echo "монет $(wc -l < $W/s2s.coins.txt) старт $(date +%T)" > $W/step2s.log
xargs -a $W/s2s.coins.txt -P "${P:-15}" -L 1 bash $W/tk402-runS.sh one; rc=$?
n=$(ls $W/s2s1000/*.npz $W/s2s5000/*.npz $W/s2s30000/*.npz 2>/dev/null | wc -l); echo "rc=$rc npz=$n конец $(date +%T)" >> $W/step2s.log
if [ "$rc" = 0 ] && [ "$n" = "$((3 * $(wc -l < $W/s2s.coins.txt)))" ]; then echo '{"done":1}' > $W/step2s.done; else touch $W/step2s.failed; fi
