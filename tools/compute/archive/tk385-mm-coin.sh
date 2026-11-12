#!/bin/bash
# TK-385 mm: одна монета Gate без DEX: prep (кэш) -> 6 прогонов (MA1: sym k=0,2/0,5 %, spr x0,5/1/2, сдвиг MA +3 ч sym 0,5 %) -> удалить кэш. Запуск: tk385-mm-coin.sh COIN
cd /data/tk0380; C=$1; O=tk385/mm; mkdir -p $O
[ -f $O/$C-done ] && exit 0
[ -f bbo/$C.csv.gz ] || npvenv/bin/python tk380-ob-bbo.py bbo $C > $O/$C-bbo.log 2>&1
npvenv/bin/python tk385-prep6.py $C 1 > $O/$C-prep.log 2>&1 || { echo prepfail >> $O/$C-prep.log; rm -rf tk385/cache/$C; exit 0; }
R() { n=$1; shift; env NULL_MIN=1 LAG_S=0 DELAY_S=1 PESS=2 "$@" npvenv/bin/python tk385-mm1.py $C bsc med $F $O/$C-$n.csv > $O/$C-$n.log 2>&1; }
F=sym; R s02 KI=0; R s05 KI=2; R sh3 KI=2 SHIFT_H=3
F=spr; R p05 KI=0; R p10 KI=2; R p20 KI=4
rm -rf tk385/cache/$C; touch $O/$C-done
