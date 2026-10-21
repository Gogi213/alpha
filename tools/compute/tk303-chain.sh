#!/usr/bin/env bash
# TK-303 (П-15 F2, В-21/В-32): PGO-сборка tk303 (r2; ключи btc_lag_78/91/104s_min) -> счёт F2 одним run-launch (L-P15F2: 75 форм + 54 по порогам месяца на сутки)
# и ПАРАЛЛЕЛЬНО гейт «байт в байт» нового бинаря против tk272d (10 клеток, одни сутки); DIFF -> счёт снимается. Ждущий скрипт (света не ест).
# Запуск на calc: systemd-run --unit tk303-chain --collect bash /data/tk0303/tk303-chain.sh ; исходники: /data/tk0303/head/src.tgz (push-head.sh), план: /data/tk0303/plan_src/plan_cells*
set -u
TAG=tk303; W=/data/tk0303; SRC=$W/src-pgo; L=$W/chain.log; B=/data/tk0272/build-pgo.sh; A=/data/sched/alsched.py
R=/data/launch/out/L-P15F2; BIN=/opt/alpha-compute/bin/alpha-$TAG-pgo
rm -f $W/chain.ok $W/chain.fail $W/gate.ok
waitrc() { [ -n "$1" ] || return 9; while [ ! -f /data/sched/rc/$1 ]; do sleep 20; done; return "$(cat /data/sched/rc/$1)"; }
fail() { echo "FAIL $1" | tee -a $L; touch $W/chain.fail; exit 1; }
sub() { python3 $A submit "$@" 2>&1 | tee -a $L | grep -o '[0-9]\{13\}' | head -1; }
rm -rf $SRC; mkdir -p $SRC && tar -xzf $W/head/src.tgz -C $SRC || fail untar
# план-каталог запуска (раскладка prepare_plan: L.cells/L.args пусты, всё по месяцам L-<ГГГГ-ММ>.*)
P=$R/plan; PS=$W/plan_src; rm -rf $R; mkdir -p $P
: > $P/L.cells; : > $P/L.args
for ym in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do
  cat $PS/plan_cells.cells $PS/plan_cells-$ym.cells > $P/L-$ym.cells
  sort -u $PS/plan_cells.args $PS/plan_cells-$ym.args > $P/L-$ym.args
  echo r2 > $P/L-$ym.bin; echo $ym >> $P/months.txt
done
cp /data/launch/out/L-P15F1/days.txt $R/days.txt
wc -l $P/L-2026-0[19].cells >> $L
u=$(sub --name tk303-pgo --kind build --cores 1 --mem 12 --max-runtime 600m --est 60m --adhoc "TK-303 PGO-сборка r2 (П-15 F2: лаги 78/91/104 с), В-32" --reason "сборка PGO, не проход счёта" --cwd $W -- env FEATURES=r2 bash $B $TAG $SRC all)
echo "pgo $u" >> $L; waitrc "$u" || fail pgo
[ -x $BIN ] || fail nobin
c=$(sub --name tk303-f2 --cores 12 --mem 24 --max-runtime 16h --est 10h --adhoc "TK-303 П-15 F2: 138 хешей x 9 мес, PGO tk303 (гейт идёт параллельно)" --reason "счёт F2 П-15 боевым PGO" --cwd /root -- env BIN=$BIN SCRIPTS=/data/tk0231 NX=12 "REASON=TK-303 П-15 F2: PGO tk303 (гейт байт в байт параллельно, DIFF = снять)" bash /opt/alpha-launch/tools/launch/run-launch.sh $R)
echo "count $c" >> $L
g=$(sub --name tk303-gate --kind gate --cores 1 --mem 12 --max-runtime 360m --est 90m --adhoc "TK-303 гейт pgo=прежний tk272d" --reason "гейт, 1 сутки 10 клеток" --cwd $W -- bash $W/tk303-gate-pgo.sh)
echo "gate $g" >> $L
waitrc "$g" || { python3 $A cancel "$c" >> $L 2>&1; fail gate; }
touch $W/gate.ok
waitrc "$c" || fail count
touch $W/chain.ok; echo OK >> $L
