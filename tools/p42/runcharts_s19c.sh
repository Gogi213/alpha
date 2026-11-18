#!/usr/bin/env bash
# П-42 §13.19 (TK-591): графики и индекс выбранного варианта — сборка принятых значений (s19c: comb_long для лонга, comb_short для шорта).
# Запуск (возврат сразу): systemd-run --unit=p42-charts-s19c -p Nice=19 -p CPUQuota=200% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/p42-t591/repo/tools/p42/runcharts_s19c.sh
# Результат: /data/p42-charts/pub_s19c (index.html, charts/ → png_s19c, ow_e до §13.19 → png_e16v2); по окончании файл pub_s19c/DONE.
set -euo pipefail
W=/data/p42-charts
R=/data/p42-t566/out/s19c
NEW=$W/s19cdetail
WORK=$W/work_s19c
PNG=$W/png_s19c
PUB=$W/pub_s19c
OLD=$W/e16detail
RULES="long:tt=20 tb=0.3 rf=0.2 rs=20;short:tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"
cd /data/p42-t591/repo/tools/p42
rm -rf "$NEW" "$WORK" "$PNG" "$PUB"
mkdir -p "$NEW/b0_lx4" "$WORK" "$PNG" "$PUB"
ln -s "$R/comb_long/long-detail.jsonl" "$NEW/b0_lx4/long-detail.jsonl"
ln -s "$R/comb_short/short-detail.jsonl" "$NEW/b0_lx4/short-detail.jsonl"
python3 charts.py features "$NEW" "$WORK" --old "$OLD" --workers 2 --rules "$RULES" > $W/features-s19c.log 2>&1
IDS_L=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='L'))")
IDS_S=$(python3 -c "
import json
print(','.join(json.loads(l)['id'] for l in open('$WORK/features.jsonl') if json.loads(l)['id'][0]=='S'))")
python3 charts.py render "$NEW" "$WORK" --only "$IDS_L" --out "$PNG" --tag L --rules "$RULES" > $W/render-s19c-L.log 2>&1 &
python3 charts.py render "$NEW" "$WORK" --only "$IDS_S" --out "$PNG" --tag S --rules "$RULES" > $W/render-s19c-S.log 2>&1 &
wait
ln -sfn "$PNG" "$PUB/charts"
mkdir -p "$PUB/ow_e до §13.19"
ln -sfn $W/png_e16v2 "$PUB/ow_e до §13.19/charts"
python3 charts.py index "$WORK" "$PUB" --charts "$PNG" --detail "$NEW" --old "$OLD" --name "сборка §13.19 (ow_e + выход по времени/возврат; шорт: + безубыток выкл., вход ближе к уровню, два входа)" \
  --oldname "ow_e, вход по ленте, §13.16" --oldfeat $W/work_e16/features.jsonl --oldcharts "ow_e до §13.19/charts" --rules "$RULES" > $W/index-s19c.log 2>&1
echo "png: $(ls $PNG/*.png | wc -l)" >> $W/index-s19c.log
touch "$PUB/DONE"
