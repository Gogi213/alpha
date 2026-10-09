#!/bin/bash
# chain53 (CEO 06.10 16:40 п.1): D20 approaches ~2/3 оставшихся на sdb файлов >200 КБ -> копия на sda5 (apprsplit2, cmp, симлинк; ~10 ГБ чтения sdb->sda), затем полный янв+фев одной очередью как chain52 (839,0 с). Откат: apprsplit2.sh --undo /data/tk048/chain53-<mon>.log
rm -f /data/tk048/chain53.done
for MON in jan feb; do P=$([ $MON = jan ] && echo 2026-01 || echo 2026-02); N=$([ $MON = jan ] && echo 31 || echo 28)
  APPR_LOG=/data/tk048/chain53-$MON.log ionice -c2 -n6 /data/tk048/apprsplit2.sh /data/tk046/$MON/home/alpha/epochs/e-$MON/study/approaches/D20 /alpha-sda/tk048/appr-m53-$MON 1 $N $P 3 200000
done
awk '{s+=$NF} END{print "moved_GB",s/1e9,"files",NR}' /data/tk048/chain53-jan.log /data/tk048/chain53-feb.log > /data/tk048/chain53-moved.txt
systemd-run --wait --collect --unit tk048-full-b14m -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grpg.sh full-b14m alpha-b14flag 8 15 > /dev/null 2>&1
touch /data/tk048/chain53.done
