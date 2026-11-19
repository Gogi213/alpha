#!/bin/bash
# Итоговое окно «после» (машинная база, метод p1/K-окон): final-window.sh <метка> <секунд> <старт HH:MM:SS UTC>
# Снимок в начале и в конце окна (cgroup cpu.stat четырёх юнитов + softirq/irq/steal машины, /proc, health четырёх коллекторов), отчёт kwin.py. Больше на srv в окне ничего не запускается.
cd /data/perf
L=${1:-final}; SECS=${2:-1800}; START=$(date -u -d "${3:-now}" +%s)
D=/data/perf/fw/$L; rm -rf $D; mkdir -p $D
until [ $(date +%s) -ge $START ]; do sleep 5; done
python3 kwin.py snap $D/a
sleep $SECS
python3 kwin.py snap $D/b
python3 kwin.py report $D/a $D/b $L > $D/report.txt 2>&1
echo ALLDONE >> $D/report.txt
