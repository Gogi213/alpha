#!/usr/bin/env bash
# TK-402 шаг 2 (бой): tk402-step2.py run по всем монетам stage1.tsv (bybit_fut не пуст), 15 процессов, сутки 15–22.10; npz по монете -> /data/tk0402/s2/<COIN>.npz.
#   Пуск — ТОЛЬКО после цитаты владельца по П-28 v3.1:
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk402-step2 --cores 15 --mem 24 --max-runtime 3h --cwd /data/tk0402 -- bash /data/tk0402/tk402-run.sh
# Метка /data/tk0402/step2.done только если все монеты дали npz или причину в охвате (B14), иначе step2.failed.
set -uo pipefail
W=/data/tk0402; PY=/data/tk0380/npvenv/bin/python; DAYS=2026-10-15,2026-10-16,2026-10-17,2026-10-18,2026-10-19,2026-10-20,2026-10-21,2026-10-22
if [ "${1:-}" = one ]; then
  c=$2; [ -s $W/s2/$c.npz ] && exit 0
  $PY $W/tk402-step2.py run "$c" $DAYS $W/s2/$c.npz.tmp.npz >> $W/s2/$c.log 2>&1 && mv $W/s2/$c.npz.tmp.npz $W/s2/$c.npz
  exit $?
fi
mkdir -p $W/s2; rm -f $W/step2.done $W/step2.failed
awk -F'\t' 'NR>1 && $2!="" {print $1}' $W/stage1.tsv > $W/s2/coins.txt
echo "монет $(wc -l < $W/s2/coins.txt) старт $(date +%T)" > $W/step2.log
xargs -a $W/s2/coins.txt -P "${P:-15}" -L 1 bash $W/tk402-run.sh one; rc=$?
n=$(ls $W/s2/*.npz 2>/dev/null | wc -l); echo "rc=$rc npz=$n конец $(date +%T)" >> $W/step2.log
if [ "$rc" = 0 ] && [ "$n" = "$(wc -l < $W/s2/coins.txt)" ]; then echo '{"done":1}' > $W/step2.done; else touch $W/step2.failed; fi
