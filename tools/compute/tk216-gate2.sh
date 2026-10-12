#!/usr/bin/env bash
# TK-216 гейт 2 (calc): месяц (все B1-сутки января), один набор t-bid-btc4h-q1 × 2 формы F1.
# A: пул (--set pool:age=2700,side=bid) -> раннер filter-cells.py; B: прямой bounce-grid (--round-memo off) -> busy-replay -> psim. sha256 closes.
#   tk216-gate2.sh  (env D=4 GT=2) -> /data/tk216/m/{wallA.txt,wallB.txt,runner-wall.txt,report.txt,done}
set -uo pipefail
M=/data/tk216/m; T=/data/tk083/tools; MO=2026-01; D=${D:-4}; export GT=${GT:-2}
F=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800; FF=$F,$F-pyfresh10; SET=t-bid-btc4h-q1; FLT=age=2700,side=bid,btc4h_max=-44.55
mkdir -p $M/planA $M/planB; cd $M || exit 2
ARGS=$(printf '%s\n' "--entry-form ladder3x0..0.0409sw2" "--entry-ttl-secs 1800" "--exit-form pyfresh10" "--stop-form pct2" "--take-form tr1x1" "--deadline-secs 14400")
{ echo "$ARGS"; echo "--set pool:age=2700,side=bid"; } > planA/f1.args
{ echo "$ARGS"; echo "--round-memo off"; echo "--set $SET:$FLT"; } > planB/f1.args
printf '%s pool\n%s-pyfresh10 pool\n' $F $F > planA/f1.cells
printf '%s %s\n%s-pyfresh10 %s\n' $F $SET $F $SET > planB/f1.cells
: > planA/f1-$MO.args; : > planA/f1-$MO.cells; : > planB/f1-$MO.args; : > planB/f1-$MO.cells
days=$(awk -F, 'NR>1 && $1=="jan"{print $2}' /data/tk0115/delta/b1-symdays.csv | sort -u)
for X in A B; do
  s=$(date +%s)
  echo "$days" | P=$M/plan$X O=$M/out$X xargs -P "$D" -I{} bash /data/tk0196/day.sh {} jan {}
  echo "wall_s=$(( $(date +%s)-s ))" > wall$X.txt
done
ok=""; for d in $days; do [ -e outA/$d/done ] && [ -e outB/$d/done ] && [ ! -s outA/$d/fail.txt ] && [ ! -s outB/$d/fail.txt ] && ok="$ok $d"; done
echo "сутки в сверке: $(echo $ok | wc -w) из $(echo $days | wc -w)" > report.txt
rm -rf tree regime; mkdir -p tree/$MO regime treeB/$MO
for d in $ok; do
  mkdir -p tree/$MO/$d/pool treeB/$MO/$d/$SET
  for f in rounds.csv signals.csv; do ln -sf $M/outA/$d/out/$d/pool/$f tree/$MO/$d/pool/$f; ln -sf $M/outB/$d/out/$d/$SET/$f treeB/$MO/$d/$SET/$f; done
  ln -sf /data/tk0196/regime/jan/$d.csv regime/$d.csv
done
printf '%s\t%s\t%s\n' $SET $FF $FLT > cells.tsv
s=$(date +%s)
python3 /data/tk216/filter-cells.py --pool-tree $M/tree/$MO --pool-set pool --pool-base age=2700,side=bid \
  --approaches '/data/p12r3a/jrn/{day}/wt/sub/D20' --regime-from $M/regime --cells cells.tsv --out $M/outR --tools $T \
  --psim-deposit 2500 --psim-position 500 --psim-max-pos 0 >> report.txt 2>&1 || { echo "FAIL runner" >> report.txt; touch done; exit 1; }
echo "runner_wall_s=$(( $(date +%s)-s ))" > runner-wall.txt
python3 $T/busy-replay.py $M/treeB/$MO $M/replayB/$MO --sets $SET >> report.txt 2>&1
python3 $T/portfolio-sim.py --epoch $MO=$M/replayB:$MO --variant "$SET@$F=$SET/$F" --variant "$SET@$F-pyfresh10=$SET/$F-pyfresh10" \
  --deposit-usd 2500 --position-usd 500 --max-pos 0 --closes-out $M/closes-B.json >> report.txt 2>&1
python3 - >> report.txt <<'PY'
import json,hashlib
def h(p):
    v=next(iter(json.load(open(p)).values()));return hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()[:16]
a=h('/data/tk216/m/outR/t-bid-btc4h-q1/closes.json');b=h('/data/tk216/m/closes-B.json');print('GATE2',a,b,'РАВНЫ' if a==b else 'РАЗНЫ')
PY
touch done
