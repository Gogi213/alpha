#!/bin/bash
# TK-015 (поправка 7): чтение месяцев янв–июн по готовности — раз в 15 мин, до 36 ч. Месяц читается, когда у всех
# суток есть .done трёх клеток p07-h1 и ворота Судьи п. 2 (D20 дека = VPS, `tk015-gate.sh` Инженера) пройдены —
# строка ИТОГ «совпало N из N, различий 0» в ~/alpha/epochs/e-<мес>/gate-*.txt. Сначала --data-gate (статусы в
# tmp-p07/h1-read/<мес>-gate.txt), при ok — --out <мес>.json; отметка tmp-p07/h1-read/READY-<мес> (или GATE-STOP-<мес>).
A=/home/deck/alpha
R=$A/tmp-p07/h1-read
mkdir -p $R
declare -A N=([jan]=31 [feb]=28 [mar]=31 [apr]=30 [may]=31 [jun]=30)
declare -A M=([jan]=01 [feb]=02 [mar]=03 [apr]=04 [may]=05 [jun]=06)
for i in $(seq 1 144); do
  left=0
  for mon in jan feb mar apr may jun; do
    [ -e $R/READY-$mon ] || [ -e $R/GATE-STOP-$mon ] && continue
    left=1
    H=$A/epochs/e-$mon
    ok=1
    for c in p07m-main p07b-base p07a-h2-fr1; do
      n=$(ls $H/b5/$c/2026-${M[$mon]}-*/.done 2>/dev/null | wc -l)
      [ "$n" -ge "${N[$mon]}" ] || ok=0
    done
    [ $ok = 1 ] || continue
    # ворота дека = VPS (tk015-gate.sh): «ИТОГ <эп> <сутки>: совпало N из N (…), различий 0»
    grep -qE "^ИТОГ .*совпало ([1-9][0-9]*) из  .*различий 0$" $H/gate-*.txt 2>/dev/null || continue
    echo "$(date -u +%FT%TZ) $mon: чтение" >> $R/autoread.log
    if python3 $A/bin/p07-h1-read.py --month $mon --data-gate > $R/$mon-gate.txt 2>&1; then
      python3 $A/bin/p07-h1-read.py --month $mon --out $R/$mon.json > $R/$mon-read.log 2>&1 && touch $R/READY-$mon
    else
      touch $R/GATE-STOP-$mon
    fi
    echo "$(date -u +%FT%TZ) $mon: $(ls $R | grep -E "^(READY|GATE-STOP)-$mon$")" >> $R/autoread.log
  done
  [ $left = 0 ] && exit 0
  sleep 900
done
