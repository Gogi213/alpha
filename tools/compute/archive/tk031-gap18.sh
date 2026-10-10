#!/bin/bash
# TK-031 (дыры 18): WIF фев 01–09 и DRAM авг 03–11 (approaches дозаказаны, AP2_DONE); копия rest11 для 1 монеты на месяц.
# (исходник: 11 монет TK-035, янв→11.08, новые бинлоги (/data/alpha/epochs/e-*/root) и approaches (/root/tk035ap/out).
# Отдельное дерево ~/alpha/epochs11g/e-<м> (свой b5), чтобы 65 монет не трогать. Скрипты суток — копии jall-<м>-<д>.sh (jallg-*).
# Запуск: tk031-rest11.sh "jan feb ..." ; DRY=1 — только виды суток. Итог: ~/alpha/tk031/gap18-summary.txt, метка GAP18_DONE.
export HOME=/home/deck; A=$HOME/alpha; O=$A/tk031; N=${N:-8}; C=$A/tmp-p07/cells-by-day; AP=/root/tk035ap/out
declare -A COIN=([feb]=WIFUSDT [aug]=DRAMUSDT) LO=([feb]=1 [aug]=3) HI=([feb]=9 [aug]=11)
declare -A MN=([jan]=1 [feb]=2 [mar]=3 [apr]=4 [may]=5 [jun]=6 [jul]=7 [aug]=8)
mkdir -p $O; rm -f $O/GAP18_DONE
for m in $1; do
  n=${MN[$m]}; mm=$(printf %02d $n); R=$A/epochs/e-$m; S=/data/alpha/epochs/e-$m; E=$A/epochs11g/e-$m
  mkdir -p $E/study $E/b5; ln -sfn $A/bin $E/bin; ln -sfn $S/root $E/root
  ln -sfn $E/study/ax $E/study/approaches  # extra-runs-файлы ссылаются на study/approaches/D20
  for x in sigma240 klines regime; do ln -sfn $(readlink -f $R/study/$x) $E/study/$x; done
  days=$(ls $S/root/ | grep -o "2026-$mm-[0-9][0-9]" | sort -u | sed "s/2026-$mm-//")
  INC=${COIN[$m]}; days=$(echo "$days" | awk -v lo=${LO[$m]} -v hi=${HI[$m]} '$1>=lo&&$1<=hi')
  : > $O/droppedg-$m.txt
  for d in $days; do
    V=$E/study/root-2026-$mm-$d; AX=$E/study/ax/D20/2026-$mm-$d; rm -rf $V $AX; mkdir -p $V $AX
    HAS=()
    for sy in $INC; do
      if [ -e $S/root/$sy-2026-$mm-$d.binlog ] && [ -e $E/study/sigma240/sigma-$sy.csv ] && [ -e $AP/2026-$mm-$d/approaches-$sy.csv ]; then
        HAS+=($sy); ln -sf $S/root/$sy-2026-$mm-$d.binlog $V/; cp $S/root/verify-$sy.status $V/ 2>/dev/null
        for p in approaches touches mids1m; do [ -e $AP/2026-$mm-$d/$p-$sy.csv ] && ln -sfn $AP/2026-$mm-$d/$p-$sy.csv $AX/$p-$sy.csv; done
      else echo "$mm-$d $sy missing" >> $O/droppedg-$m.txt; fi
    done
    printf '%s\n' "${HAS[@]}" > $AX/symbols.txt
    cp $S/root/session.json $V/
    awk -F, 'NR==FNR{h[$1]=1;next} FNR==1||($1 in h)' $AX/symbols.txt $S/root/instruments.csv > $V/instruments.csv
  done
  [ -n "$DRY" ] && { echo "$m DRY days=$(echo $days | wc -w) dropped=$(wc -l < $O/droppedg-$m.txt)" >> $O/dryg-summary.txt; continue; }
  for d in $days; do
    f=$C/jall-$m-2026-$mm-$d.sh; [ -e $f ] || { echo "$m-$d нет скрипта $f" >> $O/gap18-summary.txt; continue 2; }
    sed -e "s#study/approaches/D20#study/ax/D20#g" -e "s#cells-by-day/jall-$m-2026-$mm-$d.grid.log#cells-by-day/jallg-$m-2026-$mm-$d.grid.log#" $f > $C/jallg-$m-2026-$mm-$d.sh
  done
  rm -f $O/RG$m-*; sync
  t0=$(date +%s)
  echo $days | tr ' ' '\n' | xargs -P $N -I{} bash -c "cd $E && bash $C/jallg-$m-2026-$mm-{}.sh > $O/RG$m-{}.out 2> $O/RG$m-{}.err; echo \$? > $O/RG$m-{}.rc"
  t1=$(date +%s)
  echo "$m days=$(echo $days | wc -w) wall=$((t1-t0))s rc: $(cat $O/RG$m-*.rc | sort | uniq -c | tr '\n' ' ')" >> $O/gap18-summary.txt
done
date > $O/GAP18_DONE
