#!/bin/bash
# TK-071 smoke планировщика на живом systemd: SCHED_PAT=tk0s-* — замораживаются только задания самого планировщика
# Запуск как замер: systemd-run --unit tk071-smoke --collect /data/benchrun.sh wave bash /data/sched/sched-smoke.sh
export SCHED_DIR=/data/sched-test SCHED_PAT='tk0s-*' SCHED_TICK=2 SCHED_DEAD_S=15
A=/data/sched/alsched.py
rm -rf $SCHED_DIR; mkdir -p $SCHED_DIR; cd $SCHED_DIR
say() { echo "[$(date +%T)] $*" | tee -a $SCHED_DIR/smoke.log; }
fz() { systemctl show -p FreezerState --value "$1"; }
python3 $A daemon >daemon.log 2>&1 & DPID=$!
sleep 3
V=$(python3 $A submit --name victim --cores 2 --mem 1 --max-runtime 300 -- bash -c 'while :; do :; done')
sleep 6
U=$(systemctl list-units --no-legend --plain "tk0s-victim-*" | awk '{print $1}')
say "T0 victim=$V unit=$U active=$(systemctl show -p ActiveState --value $U) AllowedCPUs=$(systemctl show -p AllowedCPUs --value $U) RuntimeMaxUSec=$(systemctl show -p RuntimeMaxUSec --value $U)"
python3 $A wave --max-runtime 30s sleep 8 >wave1.out 2>&1 & WP=$!
sleep 6
say "T1 во время замера: victim freezer=$(fz $U) measure-units=$(systemctl list-units --no-legend --plain 'alpha-sm-*' | awk '{print $1}' | tr '\n' ' ')"
wait $WP; say "T1 wave rc=$? вывод: $(tr '\n' '|' <wave1.out)"
sleep 4
say "T1 после: victim freezer=$(fz $U) RuntimeMaxUSec=$(systemctl show -p RuntimeMaxUSec --value $U)"
say "T1 validity: $(cat validity/*.json 2>&1 | head -c 700)"
systemd-run -q --unit tk0s-ext --collect sleep 1000; systemctl freeze tk0s-ext.service
python3 $A wave --max-runtime 30s sleep 3 >wave2.out 2>&1; say "T1b две заморозки: ext freezer после моего замера=$(fz tk0s-ext.service) (ждём frozen)"
systemctl thaw tk0s-ext.service; systemctl stop tk0s-ext.service
python3 $A wave --max-runtime 30s sleep 6 >wave4.out 2>&1 & W4=$!
sleep 2; systemd-run -q --unit tk0s-late --collect bash -c 'while :; do :; done'
wait $W4; say "T1c чужая нагрузка в окне: rc=$? (ждём 3) вывод: $(tr '
' '|' <wave4.out)"; systemctl stop tk0s-late.service
S1=$(python3 $A submit --name shortlived --cores 1 --mem 1 --max-runtime 12s -- sleep 1000)
sleep 20
say "T2 max_runtime: rc=$(cat rc/$S1 2>&1) unit-active=$(systemctl is-active tk0s-shortlived-$S1)"
D=/dev/sdb
python3 $A wave --max-runtime 120s dd if=$D of=/dev/null bs=1M count=1024 skip=200000 iflag=direct >wave5.out 2>&1; R5=$?
say "T1d диск, один: rc=$R5 validity=$(ls -t validity/*.json | head -1 | xargs cut -c1-330)"
python3 $A wave --max-runtime 120s dd if=$D of=/dev/null bs=1M count=1024 skip=300000 iflag=direct >wave6.out 2>&1 & W6=$!
sleep 2; systemd-run -q --unit smk-reader --collect dd if=$D of=/dev/null bs=4k count=1000 skip=50000000 iflag=direct
wait $W6; R6=$?; say "T1e чужие мелкие чтения: rc=$R6 (ждём 3) вывод: $(grep НЕДЕЙСТВ wave6.out | head -c 300) validity=$(ls -t validity/*.json | head -1 | xargs cut -c1-330)"; systemctl stop smk-reader.service 2>/dev/null
python3 $A wave --max-runtime 120s dd if=$D of=/dev/null bs=1M count=1024 skip=400000 >wave8.out 2>&1; R8=$?
say "T1d-буф диск через кэш страниц, один: rc=$R8 (ждём 0) validity=$(ls -t validity/*.json | head -1 | xargs cut -c1-330)"
python3 $A wave --max-runtime 120s dd if=$D of=/dev/null bs=1M count=1024 skip=500000 >wave9.out 2>&1 & W9=$!
sleep 2; systemd-run -q --unit smk-reader --collect dd if=$D of=/dev/null bs=4k count=1000 skip=60000000 iflag=direct
wait $W9; R9=$?; say "T1e-буф чужие мелкие чтения: rc=$R9 (ждём 3) вывод: $(grep НЕДЕЙСТВ wave9.out | head -c 300) validity=$(ls -t validity/*.json | head -1 | xargs cut -c1-330)"; systemctl stop smk-reader.service 2>/dev/null
python3 $A wave --max-runtime 120s bash -c "dd if=$D of=/dev/null bs=1M count=50 skip=700000; sleep 55" >wave10.out 2>&1; R10=$?
say "T1f тёплая (60 с, ~50 МБ с диска): rc=$R10 (ждём 0) validity=$(ls -t validity/*.json | head -1 | xargs cut -c1-330)"
python3 $A wave --max-runtime 20s sleep 100 >wave3.out 2>&1 & WP=$!
sleep 6
say "T3 замер идёт: victim freezer=$(fz $U); failsafe timer=$(systemctl is-active alpha-sm-failsafe.timer)"
kill -9 $DPID; say "T3 демон убит"
sleep 32
say "T3 через 32 с после смерти демона: victim freezer=$(fz $U) forced-thaw=$(ls forced-thaw 2>&1 | tail -1)"
wait $WP; say "T3 CLI после смерти демона: rc=$? (ждём 4) вывод: $(tr '
' '|' <wave3.out | head -c 300)"
python3 $A daemon >daemon2.log 2>&1 & DPID=$!
sleep 3
python3 $A wave --max-runtime 60s sleep 14 >wave7.out 2>&1 & W7=$!
sleep 6; kill -9 $DPID; python3 $A daemon >daemon3.log 2>&1 & DPID=$!
sleep 3; say "T4 victim freezer внутри окна после перезапуска демона: $(fz $U) (ждём frozen)"
wait $W7; say "T4 перезапуск демона в окне: rc=$? (ждём 3) вывод: $(tr '
' '|' <wave7.out | head -c 300) victim freezer=$(fz $U)"
kill $DPID 2>/dev/null
systemctl stop 'alpha-sm-*' 2>/dev/null; systemctl stop 'tk0s-*' 2>/dev/null
say "ps:"; SCHED_DIR=$SCHED_DIR python3 $A ps | tee -a smoke.log
say "КОНЕЦ"
