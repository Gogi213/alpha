#!/usr/bin/env bash
# TK-607: цепочка ворот L1 (склад сделок) нового бинаря. Запуск на srv:
#   systemd-run --unit=tk607-gate-l1 --nice=19 -p CPUWeight=1 bash /data/p42-tk607/tk607_gate_chain.sh <BIN> l1
# Порядок (§8 п. 3, §13.24.6 п. 2): wa холодный склад → wb на складе wa (08.08–29.08 тёплые) → wc → wa повторно (всё тёплое) → rt-fz холодный (свой склад) → rt-fz тёплый.
# Все вызовы — те же варианты b0002 (16, эталон /data/p42-tk599/out/<окно>), второй пакет b0006 (11) — на тёплом складе по wb, чтобы другой набор сигнальных ключей читал/дописывал склад.
# Итог — /data/p42-tk607/gate/<метка>.summary (строки GATE OK|DIFF|FAIL); пустой склад на старте обязателен.
set -uo pipefail
BIN="$1"; TAG="$2"
D=/data/p42-tk607; V=/data/p42-tk599/var
GATE=$D/tk607_gate.sh; SUM=$D/gate/$TAG.summary
mkdir -p $D/gate; : > "$SUM"
ST=$D/store-$TAG; ST2=$D/store-rtfz-$TAG
rm -rf "$ST" "$ST2"
FL=(--tick-store-cap-gb 3 --tick-store-min-free-gb 5)
run() { # метка окно варианты [ключи...]
  local label="$1" w="$2" vars="$3"; shift 3
  echo "== $label $w $(date +%H:%M:%S)" >> "$SUM"
  REF_LOG="${REF_LOG:-}" bash "$GATE" "$BIN" "$label" "$w" "$vars" "$@" >> "$SUM" 2>&1
}
REF_LOG=/data/p42-tk599/log/b0002-wa.log run $TAG-wa-cold   wa $V/b0002.txt --tick-store "$ST" "${FL[@]}"
REF_LOG=/data/p42-tk599/log/b0002-wb.log run $TAG-wb-mixed  wb $V/b0002.txt --tick-store "$ST" "${FL[@]}"
REF_LOG=/data/p42-tk599/log/b0002-wc.log run $TAG-wc-mixed  wc $V/b0002.txt --tick-store "$ST" "${FL[@]}"
REF_LOG=/data/p42-tk599/log/b0002-wa.log run $TAG-wa-warm   wa $V/b0002.txt --tick-store "$ST" "${FL[@]}"
REF_LOG=/data/p42-tk599/log/b0006-wb.log run $TAG-wb-b6     wb $V/b0006.txt --tick-store "$ST" "${FL[@]}"
cp /data/p42-t566/variants-rt-fz.txt $D/variants-rt-fz.txt
run $TAG-rtfz-cold rtfz $D/variants-rt-fz.txt --tick-store "$ST2" "${FL[@]}"
run $TAG-rtfz-warm rtfz $D/variants-rt-fz.txt --tick-store "$ST2" "${FL[@]}"
du -sh "$ST" "$ST2" >> "$SUM" 2>&1
echo "== chain done $(date +%H:%M:%S)" >> "$SUM"
