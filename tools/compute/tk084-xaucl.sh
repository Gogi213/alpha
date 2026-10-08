#!/bin/bash
# TK-084 п.2 (В-198): докачка XAUUSDT/CLUSDT (сутки из /data/tk037/missing-days.csv), формат v4 (--steps-from-day, как 112 суток В-172) -> /data/tk037/roots/e-YYYY-MM. Журнал /data/tk084/xaucl.log, маркер xaucl.done.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --name tk084-xaucl --max-runtime 12h --cores 4 --mem 8G -- bash /data/tk084/xaucl.sh
mkdir -p /data/tk084; rm -f /data/tk084/xaucl.done; mv /data/tk084/xaucl.log /data/tk084/xaucl.log.run1 2>/dev/null
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root; export BIN=/opt/alpha-compute/bin/alpha-tk037-validate ROOT=/data/tk037/roots LOG=/data/tk084/xaucl.log DAYMODE=1 NC=1
{ id; env; curl -sSI --max-time 20 https://quote-saver.bycsi.com/orderbook/linear/XAUUSDT/2026-03-27_XAUUSDT_ob200.data.zip | head -3; df -h /data/tk037raw | tail -1; } > /data/tk084/diag.txt 2>&1
grep -E "^(XAUUSDT|CLUSDT)," /data/tk037/missing-days.csv | tr -d '\r' | tr , ' ' | xargs -P 4 -L1 bash -c '/root/tk037-one.sh $0 $1' 2>>/data/tk084/xaucl.err
touch /data/tk084/xaucl.done
touch /data/tk084/xaucl5.done
