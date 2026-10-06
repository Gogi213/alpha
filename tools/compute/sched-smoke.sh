#!/bin/bash
# TK-071 smoke планировщика на живом systemd: SCHED_PAT=tk0s-* — замораживаются только задания самого планировщика
# (боевое производство не трогаем). Запуск: systemd-run --unit tk071-smoke --collect bash /data/sched/sched-smoke.sh
export SCHED_DIR=/data/sched-test SCHED_PAT='tk0s-*' SCHED_TICK=2
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
S1=$(python3 $A submit --name shortlived --cores 1 --mem 1 --max-runtime 12s -- sleep 1000)
sleep 20
say "T2 max_runtime: rc=$(cat rc/$S1 2>&1) unit-active=$(systemctl is-active tk0s-shortlived-$S1)"
python3 $A wave --max-runtime 20s sleep 100 >wave3.out 2>&1 & WP=$!
sleep 6
say "T3 замер идёт: victim freezer=$(fz $U); failsafe timer=$(systemctl is-active alpha-sm-failsafe.timer)"
kill -9 $DPID; say "T3 демон убит"
sleep 32
say "T3 через 32 с после смерти демона: victim freezer=$(fz $U) frozen.json=$(ls frozen.json 2>&1 | tail -1)"
kill $WP 2>/dev/null
systemctl stop 'alpha-sm-*' 2>/dev/null; systemctl stop 'tk0s-*' 2>/dev/null
say "ps:"; SCHED_DIR=$SCHED_DIR python3 $A ps | tee -a smoke.log
say "КОНЕЦ"
