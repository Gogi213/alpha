#!/bin/bash
# TK-394 П-28 шаг 2ms: (1) BBO Gate 0,1 с по (монета, час) -> /data/tk0394/bboms, (2) step2ms по монетам -> /data/tk0394/$OUT, (3) метка DONE.json.
# Запуск: tk394-step2ms-run.sh СПИСОК_МОНЕТ ВЫХОД ПРОЦЕССОВ   (прод: valid39.txt step2ms 15; проба: pilot-coin.txt step2ms-pilot 1)
cd /data/tk0394
LIST=$1; OUT=$2; NP=${3:-15}
exec >>/data/tk0394/$OUT-run.log 2>&1
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610
PY=/data/tk0380/npvenv/bin/python
mkdir -p $OUT bboms
# единицы: крупные файлы первыми
for c in $(cat $LIST); do ls $OB | grep "^${c}_USDT-" | sed "s|^|$c $OB/|"; done > $OUT-units.txt
while read c f; do echo "$(stat -c %s $f) $c $f"; done < $OUT-units.txt | sort -rn | cut -d' ' -f2- > $OUT-units-sorted.txt
xargs -a $OUT-units-sorted.txt -P$NP -L1 sh -c '$0 /data/tk0394/tk394-gate-bbo-ms.py /data/tk0394/bboms "$1" "$2" >/dev/null' $PY
echo "build done $(date +%T)"
(grep -x ETH $LIST; grep -vx ETH $LIST) | while read c; do [ -s $OUT/$c.json ] || echo $c; done | xargs -r -P$NP -I{} $PY /data/tk0394/tk394-step2ms.py /data/tk0394/$OUT {}
echo "{\"done\":true,\"n\":$(ls $OUT/*.json | wc -l)}" > $OUT/DONE.json
