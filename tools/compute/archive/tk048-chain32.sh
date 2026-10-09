#!/bin/bash
# chain32: пара раскладки по занятости на 01–15.01 (apprsplit2: 2 из 3 оставшихся на sdb D20-файлов -> sda5), затем холодная волна q15 wide на b13 (сравнение с q15b13w 266,9 с)
E=/data/tk046/jan/home/alpha/epochs/e-jan/study/approaches/D20
/data/tk048/apprsplit2.sh $E /alpha-sda/tk048/appr15b 1 15 2026-01 3 200000
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b13s -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp11.sh q15b13s alpha-b13flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain32.done
