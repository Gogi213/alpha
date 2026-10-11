#!/bin/bash
# TK-071 п.3 (судья 04:41, б): пары A=A стенда d15 в окнах stand (умолчание --iso 4) ПОД ЖИВЫМ prod из очереди (не tk071-load).
# Судья 05:28: окно открываем, только когда prod занимает ≥ 8 ядер (PMIN); пара считается, если у обеих сторон ok:true И во всех
# строках util.log measure=1 обоих окон prod_cores ≥ PMIN (tk071-pcores.py), иначе — в отбраковку (reject) и повтор.
# Идём, пока не 3 годные пары или 8 ч.
#   Выход: /data/tk071/live2/{pairs.txt,summary.txt,done}; сводка — tk071-live-summary.py.
D=/data/tk071/live2; S=/data/sched; B=alpha-e36-idx
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1"
PMIN=8
mkdir -p $D; rm -f $D/done
prod() { python3 - <<'PY'
import glob, json
n = [j["name"] + ":" + str(len(j["cpus"])) for j in (json.load(open(f)) for f in glob.glob("/data/sched/jobs/*.json"))
     if j["state"] == "running" and j["cls"] == "prod" and j["name"] != "tk071-load" and not j.get("frozen_for")]
print(",".join(n))
PY
}
pc() { python3 -c "import sys;print(sum(int(x.split(':')[1]) for x in sys.argv[1].split(',') if x))" "$1"; }
jid() { grep -l "TAG=$1 " $S/jobs/*.json | head -1 | xargs -r basename -s .json; }
ok() { python3 -c "import json,sys;print(json.load(open('$S/validity/$1.json'))['ok'])" 2>/dev/null; }
good=0; t0=$(date +%s); k=0
while [ $good -lt 3 ] && [ $(( $(date +%s) - t0 )) -lt 28800 ]; do
  p=$(prod); [ -z "$p" ] || [ "$(pc "$p")" -lt $PMIN ] && { sleep 60; continue; }
  k=$((k+1)); res=""; pcs=""
  for s in a b; do
    python3 $S/alsched.py stand --recompute --why "TK-071 п.3: стенд A=A под живым prod" --max-runtime 40m bash /data/tk051/stand.sh $B d15 TAG=lv2$k$s $F > $D/lv2$k$s.out 2>&1
    i=$(jid lv2$k$s); res="$res $s=$i:$(ok $i):prod_end=$(prod)"; pcs="$pcs $s=$(python3 /data/tk071/pcores.py $i)"
  done
  mn=$(for x in $pcs; do echo "${x#*=}"; done | awk 'NR%2==1' | sort -n | head -1)
  v=reject; case "$res" in *a=*:True:*b=*:True:*) [ "${mn:--1}" -ge $PMIN ] && v=good;; esac
  echo "pair$k prod_start=$p$res pcores:$pcs verdict=$v" >> $D/pairs.txt
  [ $v = good ] && good=$((good+1))
done
python3 /data/tk071/live-summary.py > $D/summary.txt 2>&1
touch $D/done
