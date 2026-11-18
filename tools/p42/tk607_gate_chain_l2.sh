#!/usr/bin/env bash
# TK-607: ворота L2 (кэш сигналов `--sig-cache`) нового бинаря. Запуск на srv (приоритет по приказу координатора 18.11):
#   systemd-run --unit=tk607-gate-l2 --nice=0 -p CPUWeight=1000 --setenv=CAP_GB=8 bash /data/p42-tk607/tk607_gate_chain_l2.sh <BIN> l2 [склад сделок]
# Порядок: переигровка ВСЕХ пакетов TK-599 `var/b00NN.txt` (b0001…b0028, эталон `/data/p42-tk599/out/<окно>/<вариант>`, посчитан a03c91df) по окнам
# wa, wb, wc в порядке процедуры с общим пустым в начале кэшем сигналов (холодный → смешанные промахи/попадания по мере появления новых сигнальных
# ключей) → второй проход тех же вызовов (все попадания) → rt-fz холодный и тёплый (свой кэш). Сделки читаются из тёплого склада L1 (`store-l1`, сам
# прошёл ворота L1). Каждый вызов: `cmp` всех выходов и `meta.json` с эталоном (счётчики сигналов в `meta.json` идут из кэша), выход после сверки удаляется
# (`KEEP_OUT=0`), md5 остаются в `gate/<метка>/<окно>.md5`. Итог — /data/p42-tk607/gate/<метка>.summary (строки GATE OK|DIFF|FAIL).
set -uo pipefail
BIN="$1"; TAG="$2"; ST="${3:-/data/p42-tk607/store-l1}"
D=/data/p42-tk607; V=/data/p42-tk599/var
GATE=$D/tk607_gate.sh; SUM=$D/gate/$TAG.summary
mkdir -p $D/gate; : > "$SUM"
SC=$D/sigc-$TAG; SC2=$D/sigc-rtfz-$TAG; ST2=$D/store-rtfz-l1
rm -rf "$SC" "$SC2"
FL=(--tick-store-cap-gb "${CAP_GB:-8}" --tick-store-min-free-gb 5)
export KEEP_OUT=0
run() { # метка окно варианты [ключи...]
  local label="$1" w="$2" vars="$3"; shift 3
  echo "== $label $w $(date +%H:%M:%S)" >> "$SUM"
  bash "$GATE" "$BIN" "$label" "$w" "$vars" "$@" >> "$SUM" 2>&1
}
BATCHES=$(ls $V | sed -n 's/^\(b00[0-9][0-9]\)\.txt$/\1/p' | sort)
for pass in cold warm; do
  for b in $BATCHES; do
    for w in wa wb wc; do
      run $TAG-$pass-$b-$w $w $V/$b.txt --tick-store "$ST" "${FL[@]}" --sig-cache "$SC"
    done
  done
done
cp /data/p42-t566/variants-rt-fz.txt $D/variants-rt-fz.txt
run $TAG-rtfz-cold rtfz $D/variants-rt-fz.txt --tick-store "$ST2" "${FL[@]}" --sig-cache "$SC2"
run $TAG-rtfz-warm rtfz $D/variants-rt-fz.txt --tick-store "$ST2" "${FL[@]}" --sig-cache "$SC2"
du -sh "$SC" "$SC2" >> "$SUM" 2>&1
echo "== chain done $(date +%H:%M:%S)" >> "$SUM"
