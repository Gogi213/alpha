#!/usr/bin/env bash
# TK-407: отклик Gate на ход DEX по событиям (П-29) на ОСНОВНОМ наборе TK-392 (mid, mid3, mid4, mid5), окно 15–22.10, A=1/5/30/inf.
# Шаги: (1) gev2 для монет без книги (фьюч orderbooks tk379), (2) tk407-ev.py по всем, ноги из list3/list4.
#   python3 /data/sched/alsched.py submit --cls prod --name tk407-ev --cores 15 --mem 40 --max-runtime 3h --est 90m --cwd /data/tk0393/tk407 -- bash /data/tk0393/tk407/run.sh
B=/data/tk0393/tk407; K=/data/tk0380/tk392; PY=/data/tk0380/npvenv/bin/python; G=/data/tk0393/tk401/gev2all
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610
mkdir -p $B/ev $B/gev2; rm -f $B/run.done
for d in /data/tk0402/gev2/* /data/tk0393/tk401/gev2/*; do ln -sfn $d $G/$(basename $d) 2>/dev/null; done
for m in mid mid3 mid4 mid5; do ls $K/$m | sed 's/\.csv\.gz$//' | while read c; do echo "$c $m"; done; done > $B/coins.txt
cat $K/list3.txt $K/list4.txt | awk 'NF==2{print $1, $2}' > $B/legs.txt
# (1) недостающие gev2
while read c m; do [ -d $G/$c ] || { ls $OB/${c}_USDT-202610{15,16,17,18,19,20,21,22}*.csv.gz >/dev/null 2>&1 && echo $c; }; done < $B/coins.txt | sort -u > $B/gev-need.txt
# по файлам-часам (часы независимы; готовые .npy пропускаются) — хвост одной крупной монеты не держит один процесс
while read c; do for f in $OB/${c}_USDT-20261015*.csv.gz $OB/${c}_USDT-2026101[6-9]*.csv.gz $OB/${c}_USDT-2026102[0-2]*.csv.gz; do
  h=$(basename $f .csv.gz); h=${h##*-}; [ -f $B/gev2/$c/$h.npy ] || echo "$c $f"; done; done < $B/gev-need.txt > $B/gev-files.txt
xargs -a $B/gev-files.txt -P15 -L1 sh -c '$0 /data/tk0402/tk402-gate-bbo-ev.py '$B'/gev2 "$1" "$2" >/dev/null 2>&1' $PY
for d in $B/gev2/*; do [ -d $d ] && ln -sfn $d $G/$(basename $d); done
echo "gev-need $(wc -l < $B/gev-need.txt) built $(ls $B/gev2 | wc -l) $(date +%T)" > $B/gev.done
# (2) счёт
export GEV=$G
while read c m; do leg=$(awk -v c=$c '$1==c{print $2}' $B/legs.txt); echo "$c $K/$m/$c.csv.gz $B/ev/$c $leg"; done < $B/coins.txt \
 | xargs -P15 -L1 sh -c '[ -f $2.done ] && exit 0; '"$PY"' /data/tk0393/tk407-ev.py $0 $1 $2 $3 > $2.log 2>&1; echo rc=$? >> $2.log; touch $2.done'
echo "монет $(wc -l < $B/coins.txt) curve $(ls $B/ev/*-curve.csv 2>/dev/null | wc -l) $(date +%T)" > $B/run.done
