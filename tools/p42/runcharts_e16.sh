#!/usr/bin/env bash
# П-42 §13.16 (слово владельца 17.11: «по пробоям надо все сделки обновить с графиками»): графики и индекс сделок ow_e на srv.
# Запуск (возврат сразу): systemd-run --unit=p42-charts-e16 -p Nice=19 -p CPUQuota=200% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/p42-charts/repo/tools/p42/runcharts_e16.sh
# Результат: /data/p42-charts/pub_e16 (index.html, charts/ → png_e16, features.*, flagstats.*); по окончании файл pub_e16/DONE.
set -euo pipefail
W=/data/p42-charts
R=/data/p42-t566/out/e16
NEW=$W/e16detail
WORK=$W/work_e16
PNG=$W/png_e16
PUB=$W/pub_e16
OLD=$W/owdetail
cd $W/repo/tools/p42
rm -rf "$NEW" "$WORK" "$PNG" "$PUB"
mkdir -p "$NEW/b0_lx4" "$WORK" "$PNG" "$PUB"
for s in long short; do ln -s "$R/ow_e/$s-detail.jsonl" "$NEW/b0_lx4/$s-detail.jsonl"; done
python3 charts.py features "$NEW" "$WORK" --old "$OLD" --workers 2 > $W/features-e16.log 2>&1
IDS_L=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='L'))")
IDS_S=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='S'))")
python3 charts.py render "$NEW" "$WORK" --only "$IDS_L" --out "$PNG" --tag L > $W/render-e16-L.log 2>&1 &
python3 charts.py render "$NEW" "$WORK" --only "$IDS_S" --out "$PNG" --tag S > $W/render-e16-S.log 2>&1 &
wait
ln -sfn "$PNG" "$PUB/charts"
python3 charts.py index "$WORK" "$PUB" --charts "$PNG" --detail "$NEW" --old "$OLD" --name "ow + вход по ленте (W* = 5 мс, X = 0)" \
  --oldfeat $W/work_ow/features.jsonl --oldcharts "старый вход 15.11/charts" --rej "$R/ow_e" > $W/index-e16.log 2>&1
echo "png: $(ls $PNG/*.png | wc -l)" >> $W/index-e16.log
touch "$PUB/DONE"
