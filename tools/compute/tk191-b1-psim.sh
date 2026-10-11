#!/bin/bash
# TK-191: psim базы B1 (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800, v171c: drop TRUMP/TRX/BCH, cap 0 и 3, янв–сен) на вселенной R2-B
# (деревья волны TK-065 w-<сутки>/b5/r2, из них вырезаны символо-сутки r2b-failed-symdays.csv) -> /data/p12r3a/closes-b1-cap{0,3}-<мес>.json.
# Сверка: тот же psim на ПОЛНОЙ вселенной (busy /data/p12r2/v-b) cap0 -> closes-b1full-cap0-<мес>.json; итоги — b1-check.txt. Без пересчёта бэктеста (В-213). Метка tk191.done.
set -u
O=/data/p12r3a; T=/data/tk083/tools; D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
F=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800; S=t-bid-btc4h-q1; V="$S@$F=$S/$F"
cd $O || exit 2; rm -f tk191.done tk191-fail.txt
while read d m; do
  mo=${d:0:7}; [ "${mo:5:2}" -le 9 ] || continue
  mkdir -p s-b/$mo; [ -e s-b/$mo/$d ] && continue
  bad=$(awk -F, -v d=$d 'NR>1&&$1==d{print $2}' r2b-failed-symdays.csv)
  if [ -z "$bad" ]; then cp -rs $D/w-$d/b5/r2/$d s-b/$mo/; continue; fi
  mkdir -p s-b/$mo/$d; cp -rL $D/w-$d/b5/r2/$d/. s-b/$mo/$d/
  for f in s-b/$mo/$d/*/*.csv; do
    awk -F, -v b="$bad" 'BEGIN{n=split(b,a,"\n");for(i=1;i<=n;i++)x[a[i]]=1} /^#/||FNR==1||!($1 in x)' $f > $f.t && mv $f.t $f
  done
done < $D/days/days.tsv
one() { mo=$1
  [ -e b1-b/$mo/busy-replay.txt ] || python3 $T/busy-replay.py s-b/$mo b1-b/$mo > busy-b1-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> tk191-fail.txt; return; }
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/b1-b:$mo" --variant "$V" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-b1-cap$cap-$mo.json > psim-b1-cap$cap-$mo.txt 2> psim-b1-cap$cap-$mo.log || echo "FAIL b1 $cap $mo" >> tk191-fail.txt
  done
  python3 $T/portfolio-sim.py --epoch "$mo=/data/p12r2/v-b:$mo" --variant "$V" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-out closes-b1full-cap0-$mo.json > psim-b1full-cap0-$mo.txt 2> psim-b1full-cap0-$mo.log || echo "FAIL b1full $mo" >> tk191-fail.txt
}
for mo in $(ls s-b | sort); do one $mo & while [ "$(jobs -r | wc -l)" -ge 4 ]; do sleep 2; done; done
wait
python3 - > b1-check.txt <<'PY'
import json,glob
for k in ("b1full-cap0","b1-cap0","b1-cap3"):
    n=0;s=0.0
    for f in sorted(glob.glob(f"/data/p12r3a/closes-{k}-2026-0*.json")):
        j=json.load(open(f))
        for per in next(iter(j.values())).values():
            for caps in per.values():
                n+=len(caps); s+=sum(float(b) for a,b in caps)
    print(k,n,round(s,2))
PY
touch tk191.done
