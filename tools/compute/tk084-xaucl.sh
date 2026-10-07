#!/bin/bash
# TK-084 п.2 (В-198): докачка XAUUSDT/CLUSDT (сутки из /data/tk037/missing-days.csv), формат v4 (--steps-from-day, как 112 суток В-172) -> /data/tk037/roots/e-YYYY-MM. Журнал /data/tk084/xaucl.log, маркер xaucl.done.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --name tk084-xaucl --max-runtime 12h --cores 4 --mem 8G -- bash /data/tk084/xaucl.sh
mkdir -p /data/tk084; rm -f /data/tk084/xaucl.done
export BIN=/opt/alpha-compute/bin/alpha-tk037-validate ROOT=/data/tk037/roots LOG=/data/tk084/xaucl.log DAYMODE=1 NC=1
grep -E "^(XAUUSDT|CLUSDT)," /data/tk037/missing-days.csv | tr , ' ' | xargs -P 4 -L1 bash -c '/root/tk037-one.sh $0 $1' 2>/dev/null
touch /data/tk084/xaucl.done
