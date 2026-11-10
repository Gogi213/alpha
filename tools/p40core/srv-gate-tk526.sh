#!/bin/bash
# TK-526: гейт «байт в байт» — ядро OLD (боевое p40core-nopgo-age) против NEW (ветка tk526-theta-t) на 2 сутках x 20 активов через orch (L1-кэш /data/p40l1c),
# БЕЗ P40_RULE и P40_Q (умолчание = прежний счёт: постоянный θ из P40_THETAS, выход ½). Все таблицы (эпизоды, path, fills, exits, features, aux, age) обязаны совпасть. Запуск на srv: systemd-run --unit=p40-gate526 bash <этот файл>.
OLD=${OLD:-/root/p40core/bin/p40core-nopgo-age}
NEW=${NEW:-/root/p40core-tk526/rs/target/release/p40core}
D=${D:-/root/p40core-tk526}
O=${O:-/root/p40gate526}
DAYS=${DAYS:-2026-09-17,2026-09-18}
PAIRS=/root/p40core/pairs_d1_v3_nobitget.csv
# первые 20 активов с 1–2 парами (не крупные: пик shm у больших активов > 20 ГБ)
ASSETS=$(python3 - "$PAIRS" <<'PY'
import csv, sys, collections
n = collections.Counter(r["asset"] for r in csv.DictReader(open(sys.argv[1])))
print(",".join([a for a, c in n.items() if c <= 2][:20]))
PY
)
echo "assets=$ASSETS days=$DAYS"
mkdir -p $O
for v in old new; do
  core=$OLD; [ $v = new ] && core=$NEW
  rm -rf $O/$v
  P40_CORE=$core P40_L1=1 P40_NOTRADE=through P40_SHM_FRAC=${SHM_FRAC:-0.3} P40_L1_CACHE=/data/p40l1c nice -n 19 \
    python3 $D/orch.py $PAIRS $O/$v --days $DAYS --chunk 2 --jobs 3 --l1-p 2 --box-conn 4 --assets $ASSETS --age --l1-trades-raw --shm /dev/shm/p40g-$v > $O/$v.log 2>&1
  echo "$v rc=$?"
done
diff -rq --exclude='*.log' --exclude='DONE' --exclude='*.txt' $O/old $O/new > $O/diff.txt; echo "diff rc=$? строк: $(wc -l < $O/diff.txt)"
echo "файлов old: $(find $O/old -type f -name '*.csv' | wc -l), new: $(find $O/new -type f -name '*.csv' | wc -l)"
date > $O/GATE526_DONE
