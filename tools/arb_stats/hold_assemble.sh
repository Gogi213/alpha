#!/bin/bash
# Сборка окна arb-holding: книги четырёх бирж = /data/arb-tokyo/full/b (полное окно Tokyo) + хвост до «сейчас» (состояние разбора копируется из /data/arb-tokyo/tailb, исходное не трогаем),
# Gate — hold_gate_parse.py (дочитывает с места остановки); t1 = минимум по биржам последней записи − 2 мин; пропуски сбора — hold_gaps.py. Запуск юнитом systemd-run: bash hold_assemble.sh
set -euo pipefail
W=/data/arb-hold
S=/data/arb-tokyo
T=/opt/arb-hold/tools/arb_stats
cd $T
mkdir -p $W/tailb/parsed $W/b2/parsed
[ -d $W/tailb/state ] || cp -a $S/tailb/state $W/tailb/state
for ex in binance bybit okx bingx; do
  mkdir -p $W/tailb/parsed/$ex $W/b2/parsed/$ex
  [ -d $S/tailb/parsed/$ex ] && cp -al --no-clobber $S/tailb/parsed/$ex/. $W/tailb/parsed/$ex/ || true
done
echo "tail books"; python3 -c "import arb_live,time; print(arb_live.parse_all('$W/tailb', int(time.time()*1000), 3))"
echo "gate books"; python3 hold_gate_parse.py $W/gate 3
for ex in binance bybit okx bingx; do
  for src in $S/full/b/parsed $W/tailb/parsed; do
    [ -d $src/$ex ] && cp -al --no-clobber $src/$ex/. $W/b2/parsed/$ex/ || true
  done
done
ln -sfn $W/gate/parsed/gate $W/b2/parsed/gate
python3 hold_gaps.py $W/b2/parsed 1794550440000 $W/window.json $W/dead.json 15 | tee $W/gaps.txt
echo DONE
