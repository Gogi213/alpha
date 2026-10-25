#!/usr/bin/env bash
# TK-407, возврат Судьи 26.10 03:31: Solana основного набора (70 монет mid5) на swaps3 (пара vault'ов, дедуп) вместо mid5 TK-380; Холм заново по всему набору.
#   python3 /data/sched/alsched.py submit --cls prod --name tk407-sol3 --cores 15 --mem 50 --max-runtime 8h --est 4h --cwd /data/tk0393/tk407 -- bash /data/tk0393/tk407/sol3.sh
# (1) swaps3: 8 суток × 2 половины списка монет = 16 задач, 15 параллельно; (2) середина mid-sol3 (дедуп); (3) tk407-ev по 70 монетам; (4) агрегат A=5/1/30 (evm=evE sol=evS3).
export PYTHONUTF8=1 LC_ALL=C.UTF-8
B=/data/tk0393/tk407; S=/data/tk0393; PY=/data/tk0380/npvenv/bin/python; O=$B/s3; cd $S || exit 1
rm -f $B/sol3.done; mkdir -p $O $B/evS3
ls /data/tk0380/tk392/mid5 | sed 's/\.csv\.gz$//' | sort > $B/sol-coins.txt
split -n l/2 -d $B/sol-coins.txt $B/sol-half.
for h in 00 01; do paste -sd, $B/sol-half.$h > $B/sol-half.$h.csv; done
for d in 15 16 17 18 19 20 21 22; do for h in 00 01; do echo "$d $h"; done; done \
 | xargs -P15 -L1 sh -c '[ -f '$O'/$0$1.done ] && exit 0; COINS=$(cat '$B'/sol-half.$1.csv) SRC=gate30k python3 tk378-sol-swaps3.py mints-tk393-sol.csv '$O'/$0$1-swaps.csv '$O'/$0$1-cov.csv sqd7/sol/seg/202610$0-*.csv > '$O'/$0$1.log 2>&1 && touch '$O'/$0$1.done'
n=$(ls $O/*.done 2>/dev/null | wc -l); [ "$n" = 16 ] || { echo "swaps3 готово $n/16" > $B/sol3.fail; exit 1; }
AGG=med $PY $S/tk407-mid-sol3.py $B | tee $B/mid-sol3.log
export GEV=/data/tk0393/tk401/gev2all
while read c leg; do leg=${leg:--}; echo "$c $B/mid-sol3-med/$c.csv.gz $B/evS3/$c $leg"; done < $B/list-sol3-med.txt \
 | xargs -P15 -L1 sh -c '[ -f $2.done ] && exit 0; '"$PY"' /data/tk0393/tk407-ev.py $0 $1 $2 $3 > $2.log 2>&1; echo rc=$? >> $2.log; touch $2.done'
for A in "" 1 30; do $PY $B/agg$A.py $B/outS3A$A evm=$B/evE sol=$B/evS3 > $B/outS3A$A.txt 2>&1; done
echo "sol3 монет $(ls $B/evS3/*-curve.csv | wc -l) $(date +%T)" > $B/sol3.done
