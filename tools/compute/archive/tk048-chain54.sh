#!/bin/bash
# chain54 (CEO 06.10 п.2): откат раскладки chain53 (--undo, копии остаются), затем ABA по max_sectors_kb на холодной волне q15 b14 (янв 1–15, grp11, G=8 P=15): 1280 / 4096 / 1280. Значение возвращается 1280 в любом случае.
rm -f /data/tk048/chain54.done
restore() { for d in sda sdb; do echo 1280 > /sys/block/$d/queue/max_sectors_kb; done; }; trap restore EXIT
for m in jan feb; do /data/tk048/apprsplit2.sh --undo /data/tk048/chain53-$m.log; done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for cfg in "a 1280" "b 4096" "c 1280"; do set -- $cfg
  for d in sda sdb; do echo $2 > /sys/block/$d/queue/max_sectors_kb; done
  { echo "msk $1 $2 $(cat /sys/block/sdb/queue/max_sectors_kb) $(cat /sys/block/sda/queue/max_sectors_kb)"; } >> /data/tk048/chain54.txt
  systemd-run --wait --collect --unit tk048-q15m$1 -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp11.sh q15m$1 alpha-b14flag $D15 8 15 > /dev/null 2>&1
done
touch /data/tk048/chain54.done
