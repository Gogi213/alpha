#!/bin/bash
# binwatch-auto.sh <since_epoch> — сторож book03/book05 Binance; при TRIGGER — автооткат collect-binance (rollback.sh), строка ROLLED в журнале.
SINCE=$1; LOG=/data/perf/next-deploy/binwatch.log
python3 -u /data/perf/next-deploy/tools/binwatch.py --root /data/collect/binance/data --since $SINCE --watch book03,book05 | while IFS= read -r l; do
  echo "$l" >> $LOG
  case "$l" in *TRIGGER*)
    echo "$(date -u +%FT%TZ) AUTO-ROLLBACK collect-binance" >> $LOG
    /data/perf/rollback.sh collect-binance >> $LOG 2>&1
    echo "$(date -u +%FT%TZ) ROLLED" >> $LOG
    break;;
  esac
done
