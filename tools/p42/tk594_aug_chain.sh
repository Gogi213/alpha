#!/usr/bin/env bash
# П-42 §13.20 п. 7 (TK-594): цепочка августа 2026 на srv — деревья (минутки zip → csv, сделки — /data/p42-past) → кэш минуток (v20t0, P42_T0=2026-07-18) → движок v20t0 (только сигналы и дампы,
# b0 + b0p0, сутки 14…44 = 01–31.08) → эпизоды (sigfeat_run / join) → признаки и контроль (tk592_build) → признаки §13.20 (поток и производные).
# Метки и контроли в логах не печатаются; таблица `aug_u0.parquet` не открывается до заморозки (§13.20 п. 7).
# Запуск: systemd-run --unit=tk594-aug -p WorkingDirectory=/data/tk594 -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/tk594/py/tk594_aug_chain.sh
# Готовность: /data/tk594/aug.rc (0 = успех); лог /data/tk594/aug/log. Требует докачанных 18.07…31.08 (/data/p42-past) и metrics/premium/fundingRate (07–08) и пройденных ворот движка (egate.rc = 0).
set -uo pipefail
W=/data/tk594/aug; BIN=/data/tk594/bin/alpha-p42-v20t0; PY=/data/tk594/py
export P42_T0=2026-07-18 SIGFEAT_BASE_DATE=2026-07-18 SIGFEAT_DAYS=80 SIGFEAT_CACHE=$W/cache SIGFEAT_TRADES=/data/p42-past/aggtrades TK594_TRADES=/data/p42-past/aggtrades
cd /data/tk594; rm -f aug.rc; mkdir -p $W; : > $W/log
log() { echo "$(date -u +%T) $*" >> $W/log; }
fail() { log "СТОП: $*"; echo 1 > /data/tk594/aug.rc; exit 1; }
[ "$(cat /data/tk594/egate.rc 2>/dev/null)" = 0 ] || fail "ворота движка не пройдены (egate.rc)"
tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' > $W/symbols.list
if [ "${AUG_SKIP_PREP:-0}" != 1 ]; then
log "деревья"
rm -rf $W/klines; mkdir -p $W/klines
while read -r s; do
  mkdir -p $W/klines/$s
  for z in /data/p42-past/klines/$s/*-1m-*.zip; do [ -e "$z" ] || continue; d=$(basename "$z" .zip); d=${d##*-1m-}; unzip -p "$z" > $W/klines/$s/$d.csv.tmp && mv $W/klines/$s/$d.csv.tmp $W/klines/$s/$d.csv; done
done < $W/symbols.list
log "минуток BTCUSDT: $(ls $W/klines/BTCUSDT/*.csv | wc -l); сделок BTCUSDT: $(ls /data/p42-past/aggtrades/BTCUSDT/*.zip | wc -l)"
log "convert"
rm -rf $W/cache; $BIN breakout convert --src $W/klines --out $W/cache --threads 6 >> $W/log 2>&1 || fail convert
fi
log "движок v20t0"
printf 'b0 lat=4 latx=4\nb0p0 lat=4 latx=4 natr_min=0 t1h=0\n' > $W/variants.txt
rm -rf $W/out
env P42_SIGSX=14:44 P42_LEVELS=1 P42_SIGSX_ONLY=1 $BIN breakout tick --cache $W/cache --trades /data/p42-past/aggtrades --variants $W/variants.txt \
  --symbols "$(cat /data/p42-t566/symbols.txt)" --out $W/out/eng --threads 6 > $W/eng.log 2>&1 || fail "движок"
log "эпизоды (сутки ≥ 14)"
rm -rf $W/hold; mkdir -p $W/hold
env SIGFEAT_MIN_DAY=14 python3 $PY/sigfeat_run.py $W/out/eng $W/hold --procs 6 > $W/hold/run.log 2>&1 || fail sigfeat_run
env SIGFEAT_MIN_DAY=14 python3 $PY/sigfeat_join.py $W/out/eng $W/hold > $W/hold/join.log 2>&1 || fail sigfeat_join
log "признаки и контроль (tk592_build)"
python3 $PY/tk592_build.py $W/hold/episodes.parquet /data/tk594/aug_u0.parquet --cache $W/cache --trades /data/p42-past/aggtrades --min-day 14 --max-day 44 --procs 6 > $W/hold/build.log 2>&1 || fail tk592_build
log "признаки §13.20 — поток"
python3 $PY/tk594_feat.py /data/tk594/aug_u0.parquet /data/tk594/aug_feat_flow.parquet --mode flow --base-date 2026-07-18 --procs 6 > $W/feat_flow.log 2>&1 || fail feat_flow
log "признаки §13.20 — производные"
python3 $PY/tk594_feat.py /data/tk594/aug_u0.parquet /data/tk594/aug_feat_deriv.parquet --mode deriv --base-date 2026-07-18 --procs 6 > $W/feat_deriv.log 2>&1 || fail feat_deriv
log "готово: /data/tk594/aug_u0.parquet (+ aug_feat_*.parquet) — не открывать столбцы меток до заморозки"
echo 0 > /data/tk594/aug.rc
