#!/bin/bash
# TK-525 (возврат Судьи): OKX ETH-USDT-SWAP 21,22.10 — сырьё на боксе есть, в L1 не вошло. Пересборка теми же p40-raw-conv-tk525 (STRICT, REDO),
# выгрузка p40-l1-up (DONE есть → после выгрузки сам выходит), повтор аудита. Юнитом на srv; метка /data/p40l1/OKXFIX525_DONE, итог rc — OKXFIX525_RC.
O=/data/p40l1; rm -f $O/OKXFIX525_DONE $O/OKXFIX525_RC $O/fail_raw525c.tsv
EXF=okx_futures REDO=1 TAG=525c JOBS=2 ONLY=ETH-USDT-SWAP/2026-10-21,ETH-USDT-SWAP/2026-10-22 nice -n 12 python3 /root/p40-raw-conv-tk525.py
RC=0; [ -s $O/fail_raw525c.tsv ] && RC=1
if [ $RC = 0 ]; then
  UPN=2 nice -n 12 python3 /root/p40-l1-up.py
  bash /root/srv-tk525-audit.sh
fi
echo $RC > $O/OKXFIX525_RC; date +%F_%T > $O/OKXFIX525_DONE
