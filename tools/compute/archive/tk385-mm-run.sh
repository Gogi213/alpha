#!/bin/bash
# TK-385 mm (CEO 23:31, hypothesis «маркетмейкинг на тонких фьючах Gate» без DEX): все фьючи Gate с стаканом 15–22.10, кроме 7 уже посчитанных; линия = MA1 Gate mid, PESS=2 (хвост уровня), задержка 1 с. 15 воркеров, самые тяжёлые монеты первыми.
cd /data/tk0380; mkdir -p tk385/mm
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610
ls -S $OB | sed 's/_USDT-.*//' | awk '!s[$0]++' | grep -v -x -E 'Q|ZEC|SUE|LAPTOP|BTR|DGAI|CAKE' | while read c; do [ -d /srv/alpha-sda/tk372-gate/raw/fut/${c}_USDT ] && echo $c; done > tk385/mm-coins.txt
wc -l < tk385/mm-coins.txt
xargs -P 15 -L 1 bash tk385-mm-coin.sh < tk385/mm-coins.txt
touch tk385/mm.done
