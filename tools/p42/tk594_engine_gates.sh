#!/usr/bin/env bash
# П-42 §13.20 п. 7 (TK-594): ворота движка v20t0 на 21 монете (каждая 28-я из symbols.txt).
#  (а) без P42_T0 → `sigsx`/`levels` суток ≤ 73 как у v19 (правило `end` TK-592); с явным P42_T0=2026-09-01 — байт в байт как без него;
#  (б) P42_T0=2026-07-18 на данных сентября (сутки сетки 59…79 = 15.09…05.10): строки v19 после сдвига на 45 суток против v20t0, различия — только у уровней со «старым» пивотом (до 01.09).
# Запуск: systemd-run --unit=tk594-egate -p WorkingDirectory=/data/tk594 -p Nice=19 -p CPUQuota=400% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/tk594/py/tk594_engine_gates.sh
# Готовность: /data/tk594/egate.rc (0 = успех); результаты /data/tk594/egate/*.json. Требует докачанных суток 18.07…31.08 (/data/p42-past) для этих монет.
set -uo pipefail
MODE=${1:-all}   # a — только ворота (а) (данные августа не нужны); b — только (б) (нужен /data/p42-past); all — оба
DOA=0; DOB=0; case $MODE in a) DOA=1;; b) DOB=1;; all) DOA=1; DOB=1;; *) echo 'режим: a|b|all'; exit 2;; esac
W=/data/tk594/egate; BIN=/data/tk594/bin/alpha-p42-v20t0; PY=/data/tk594/py
cd /data/tk594; mkdir -p $W; rm -f egate.rc; [ $DOA = 1 ] && rm -f egate_a.rc; [ $DOB = 1 ] && rm -f egate_b.rc; : >> $W/log
log() { echo "$(date -u +%T) $*" >> $W/log; }
fail() { log "СТОП: $*"; echo 1 > /data/tk594/egate_${MODE}.rc; [ $DOA = 1 ] && echo 1 > /data/tk594/egate_a.rc; [ $DOB = 1 ] && echo 1 > /data/tk594/egate_b.rc; echo 1 > /data/tk594/egate.rc; exit 1; }
tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' | awk 'NR%28==1' > $W/coins.txt
SYMS=$(paste -sd',' $W/coins.txt)
printf 'b0 lat=4 latx=4\nb0p0 lat=4 latx=4 natr_min=0 t1h=0\n' > $W/variants.txt
# дерево А: сентябрь (минутки до 13.11 — как у v19; сделки — /data/aggtrades/binance)
[ $DOA = 1 ] && { rm -rf $W/kA $W/cA $W/outA $W/outA2; mkdir -p $W/kA; }; [ $DOB = 1 ] && { rm -rf $W/kB $W/cB $W/outB; mkdir -p $W/kB; }
while read -r s; do
  mkdir -p $W/kA/$s; [ $DOB = 1 ] && mkdir -p $W/kB/$s
  ln -sf /data/klines/binance/$s/20??-??-??.csv $W/kA/$s/ 2>/dev/null
  ln -sf /data/klines/binance/$s/funding.csv $W/kA/$s/funding.csv
  # дерево Б: + июль–август из /data/p42-past (zip → csv); фандинг — без (в режиме «только сигналы» не нужен)
  [ $DOB = 1 ] || continue
  ln -sf /data/klines/binance/$s/20??-??-??.csv $W/kB/$s/ 2>/dev/null
  for z in /data/p42-past/klines/$s/*-1m-*.zip; do [ -e "$z" ] || continue; d=$(basename "$z" .zip); d=${d##*-1m-}; unzip -p "$z" > $W/kB/$s/$d.csv; done
done < $W/coins.txt
[ $DOA = 1 ] && { log "convert A (по умолчанию)"; $BIN breakout convert --src $W/kA --out $W/cA --threads 6 >> $W/log 2>&1 || fail convertA ; }
[ $DOB = 1 ] && { log "convert B (P42_T0=2026-07-18)"; P42_T0=2026-07-18 $BIN breakout convert --src $W/kB --out $W/cB --threads 6 >> $W/log 2>&1 || fail convertB ; }
# деревья сделок: A — /data/aggtrades/binance; B — объединение июль–август (/data/p42-past) + сентябрь
[ $DOB = 1 ] && { mkdir -p $W/tB; while read -r s; do mkdir -p $W/tB/$s; ln -sf /data/aggtrades/binance/$s/*.zip $W/tB/$s/; ln -sf /data/p42-past/aggtrades/$s/*.zip $W/tB/$s/ 2>/dev/null; done < $W/coins.txt ; }
EO="P42_SIGSX=14:73 P42_LEVELS=1 P42_SIGSX_ONLY=1"
[ $DOA = 1 ] && { log "движок A (умолчание)";  env $EO $BIN breakout tick --cache $W/cA --trades /data/aggtrades/binance --variants $W/variants.txt --symbols "$SYMS" --out $W/outA --threads 6 > $W/engA.log 2>&1 || fail engA ; }
[ $DOA = 1 ] && { log "движок A2 (явный P42_T0=2026-09-01)"; env $EO P42_T0=2026-09-01 $BIN breakout tick --cache $W/cA --trades /data/aggtrades/binance --variants $W/variants.txt --symbols "$SYMS" --out $W/outA2 --threads 6 > $W/engA2.log 2>&1 || fail engA2 ; }
[ $DOB = 1 ] && { log "движок B (P42_T0=2026-07-18)"; env P42_SIGSX=14:79 P42_LEVELS=1 P42_SIGSX_ONLY=1 P42_T0=2026-07-18 $BIN breakout tick --cache $W/cB --trades $W/tB --variants $W/variants.txt --symbols "$SYMS" --out $W/outB --threads 6 > $W/engB.log 2>&1 || fail engB ; }
# v19 — только эти монеты
python3 - "$W" <<'PY'
import sys, pandas as pd
W = sys.argv[1]
coins = set(open(f"{W}/coins.txt").read().split())
import os
for var in ("b0", "b0p0"):
    os.makedirs(f"{W}/v19/{var}", exist_ok=True)
    for side in ("long", "short"):
        for kind in ("sigsx", "levels"):
            src = f"/data/p42-t588/out/eng/{var}/{side}-{kind}.csv"
            with open(src, encoding="utf-8") as fh, open(f"{W}/v19/{var}/{side}-{kind}.csv", "w", encoding="utf-8") as out:
                out.write(fh.readline())
                for line in fh:
                    if line.split(",", 1)[0] in coins:
                        out.write(line)
PY
if [ $DOA = 1 ]; then
log "ворота (а)"
python3 $PY/tk592_hold_gates.py sigsx $W/v19 $W/outA $W/gate_a_v19.json >> $W/log 2>&1 || fail "(а) v20t0 ≠ v19 на сутках ≤ 73"
python3 $PY/tk594_engine_gates.py same $W/outA $W/outA2 $W/gate_a_explicit.json >> $W/log 2>&1 || fail "(а) явный P42_T0 ≠ умолчание"
echo 0 > /data/tk594/egate_a.rc; fi
if [ $DOB = 1 ]; then
log "ворота (б)"
python3 $PY/tk594_engine_gates.py shift $W/v19 $W/outB $W/gate_b.json >> $W/log 2>&1 || fail "(б) необъяснённые различия"
echo 0 > /data/tk594/egate_b.rc; fi
log "готово ($MODE)"; [ "$(cat /data/tk594/egate_a.rc 2>/dev/null)" = 0 ] && [ "$(cat /data/tk594/egate_b.rc 2>/dev/null)" = 0 ] && echo 0 > /data/tk594/egate.rc
