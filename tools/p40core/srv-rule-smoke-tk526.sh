#!/bin/bash
# TK-526: смок переменного порога на 8 активах x 2 суток (L1-кэш): (A) постоянный θ=40 против (B) a0 Φ=20 q=½ (θ_t = 40, где K_t ≤ 20) — должны почти совпасть; (C) A3 24 ч; (D) A2 24 ч; (E) A3 с q=¼; (F) A4 EWMA hl=6 ч p=0,95; (G) A2 + флаги возраста (smax=120 с, u=30 с — механика, не выведенные G0 значения).
# Итог $O/summary.txt: по варианту — число строк таблиц эпизодов и время orch.
NEW=${NEW:-/root/p40core-tk526/rs/target/release/p40core}
D=${D:-/root/p40core-tk526}
O=${O:-/root/p40rule526}
DAYS=${DAYS:-2026-09-17,2026-09-18}
PAIRS=/root/p40core/pairs_d1_v3_nobitget.csv
ASSETS=$(python3 - "$PAIRS" <<'PY'
import csv, sys, collections
n = collections.Counter(r["asset"] for r in csv.DictReader(open(sys.argv[1])))
print(",".join([a for a, c in n.items() if c <= 2][:8]))
PY
)
mkdir -p $O; [ -n "$ONLY" ] || : > $O/summary.txt
run() {  # имя, окружение...
  v=$1; shift
  [ -z "$ONLY" ] || [[ "$ONLY" == *"$v"* ]] || return  # ONLY=FG — только варианты F и G
  rm -rf $O/$v; s=$(date +%s)
  env "$@" P40_CORE=$NEW P40_L1=1 P40_NOTRADE=through P40_SHM_FRAC=${SHM_FRAC:-0.3} P40_L1_CACHE=/data/p40l1c nice -n 19 \
    python3 $D/orch.py $PAIRS $O/$v --days $DAYS --chunk 2 --jobs 3 --l1-p 2 --box-conn 4 --assets $ASSETS --shm /dev/shm/p40r-$v > $O/$v.log 2>&1
  rc=$?; e=$(date +%s)
  n=$(find $O/$v -type f -regex '.*/[0-9]+$' -exec cat {} + | grep -vc '^theta')
  x=$(find $O/$v -type f -name '*.exits.csv' -exec cat {} + | grep -vc '^theta')
  echo "$v: rc=$rc эпизодов=$n выходов=$x orch=$((e-s)) с" >> $O/summary.txt
}
run A P40_THETAS=40; run B P40_RULE=a0,phi=20
run C P40_RULE=a3,phi=20,w=86400,k=3,xs=50,warm=3600
run D P40_RULE=a2,phi=20,w=86400,p=0.95,xs=50,warm=3600
run E P40_RULE=a3,phi=20,w=86400,k=3,xs=50,warm=3600 P40_Q=0.25
run F P40_RULE=a4,phi=20,hl=21600,p=0.95,xs=50,warm=3600
run G P40_RULE=a2,phi=20,w=86400,p=0.95,xs=50,smax=120,u=30,warm=3600
run H P40_RULE=a5,phi=20,w=86400,k=3,xs=50,warm=3600
date > $O/RULE526_DONE$ONLY
