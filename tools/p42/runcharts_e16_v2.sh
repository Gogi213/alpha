#!/usr/bin/env bash
# П-42 TK-589 (правка графиков 17.11): перерисовка графиков сделок ow_e — линия/полоса уровня с рождения, второй график «приближение» на сделку,
# столбцы «разброс отскоков», «касание до входа». Признаки и кэш тиков берутся из work_e16 (runcharts_e16.sh), пересчёта признаков нет.
# Запуск (возврат сразу): systemd-run --unit=p42-charts-e16v2 -p Nice=19 -p CPUQuota=200% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/p42-charts/repo_v2/tools/p42/runcharts_e16_v2.sh
# Результат: /data/p42-charts/pub_e16v2 (index.html, charts/ → png_e16v2, features.*, flagstats.*); по окончании файл pub_e16v2/DONE.
set -euo pipefail
W=/data/p42-charts
R=/data/p42-t566/out/e16
NEW=$W/e16detail
WORK=$W/work_e16
PNG=$W/png_e16v2
PUB=$W/pub_e16v2
OLD=$W/owdetail
cd $W/repo_v2/tools/p42
rm -rf "$PNG" "$PUB"
mkdir -p "$PNG" "$PUB"
IDS_L=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='L'))")
IDS_S=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='S'))")
python3 charts.py render "$NEW" "$WORK" --only "$IDS_L" --out "$PNG" --tag L > $W/render-e16v2-L.log 2>&1 &
python3 charts.py render "$NEW" "$WORK" --only "$IDS_S" --out "$PNG" --tag S > $W/render-e16v2-S.log 2>&1 &
wait
ln -sfn "$PNG" "$PUB/charts"
python3 charts.py index "$WORK" "$PUB" --charts "$PNG" --detail "$NEW" --old "$OLD" --name "ow + вход по ленте (W* = 5 мс, X = 0)" \
  --oldfeat $W/work_ow/features.jsonl --oldcharts "старый вход 15.11/charts" --rej "$R/ow_e" > $W/index-e16v2.log 2>&1
echo "png: $(ls $PNG/*.png | wc -l)" >> $W/index-e16v2.log
touch "$PUB/DONE"
