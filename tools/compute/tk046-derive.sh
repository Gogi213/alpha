#!/bin/bash
# tk046-derive.sh: этап (а) TK-046 — подходы D20 для новых монето-суток августа (список /data/tk046/aug-new.txt) + прогресс /data/progress/tk046.json
set -u; P=${P:-14}; L=/data/tk046/aug-new.txt; T=$(wc -l < $L); export O=/data/tk046/aug/study; mkdir -p $O /data/progress
rm -f $O/failed.txt; t0=$(date +%s)
( while [ ! -e $O/.ap_done ]; do n=$(ls $O/approaches/D20/*/approaches-*.csv 2>/dev/null | wc -l)
  echo "{\"ticket\":\"TK-046\",\"step\":\"август: подходы D20 новых монет\",\"done\":$n,\"total\":$T,\"unit\":\"монето-сутки\",\"next\":\"klines+sigma, regime, тест месяца\",\"updated\":\"$(date -Is)\"}" > /data/progress/tk046.json.tmp && mv /data/progress/tk046.json.tmp /data/progress/tk046.json; sleep 20; done ) &
xargs -a $L -P $P -L1 /opt/alpha-compute/bin/tk046-ap1.sh
echo "$(( $(date +%s) - t0 ))" > $O/ap_wall_s; touch $O/.ap_done; wait
