#!/bin/bash
# TK-031: месяцы merge-v3 скользящим окном (как tk031-month2.sh), N=8. Запуск: tk031-rest.sh "feb mar apr" [метка-ожидания]
# Производные: /data/alpha/derived/deck-study/epochs/e-<m>/study; root-виды: /data/alpha/epochs/e-<m>/root. Итог: ~/alpha/tk031/rest-summary.txt, метка REST_DONE.
export HOME=/home/deck; A=$HOME/alpha; O=$A/tk031; N=${N:-8}; W=${W:-10}; D=/data/alpha/derived/deck-study/epochs
# TK-035: 11 монет с битым тиком импорта — без них до 11.08 включительно
EXCL="HYPEUSDT|NEARUSDT|XLMUSDT|LITUSDT|PENDLEUSDT|TRXUSDT|PUMPFUNUSDT|ATOMUSDT|JUPUSDT|WIFUSDT|DRAMUSDT"
[ -n "$2" ] && while [ ! -e "$2" ]; do sleep 30; done
mkdir -p $O; rm -f $O/REST_DONE
declare -A MN=([jan]=1 [feb]=2 [mar]=3 [apr]=4 [may]=5 [jun]=6 [jul]=7 [aug]=8 [sep]=9)
# TK-031: месяц стартует, когда его study доехал (rsync идёт по именам: готов, если уже есть каталог следующего по порядку) или DERIVED_DONE; сентябрь — только DERIVED_DONE
ready() { [ -e /data/pull/DERIVED_DONE ] && return 0; [ "$1" = sep ] && return 1; local e=e-$1; ls $D | LC_ALL=C sort | awk -v e=$e '$0==e{f=1;next} f{ok=1} END{exit !ok}'; }
for m in $1; do
  while ! ready $m; do sleep 30; done
  n=${MN[$m]}; mm=$(printf %02d $n); E=$A/epochs/e-$m; S=/data/alpha/epochs/e-$m; mkdir -p $E/study $E/b5; ln -sfn $A/bin $E/bin; ln -sfn $S/root $E/root; cd $E
  for x in approaches regime klines sigma240; do [ -e $D/e-$m/study/$x ] && { [ $x != approaches ] || [ ! -d /data/alpha/derived/tk015/e-$m/D20 ]; } && ln -sfn $D/e-$m/study/$x $E/study/$x; done
  # feb–jun: файлы подходов лежат в tk015 (в deck-study только битые ссылки на ~/sb), как у января
  T=/data/alpha/derived/tk015/e-$m/D20
  if [ -d $T ]; then [ -L $E/study/approaches ] && mv $E/study/approaches $E/study/approaches-oldlink-$$; mkdir -p $E/study/approaches/D20; for dd in $(ls $T); do ln -sfn $T/$dd $E/study/approaches/D20/$dd; done; fi
  days=$(ls $S/root/ | grep -o "2026-$mm-[0-9][0-9]" | sort -u | sed "s/2026-$mm-//")
  [ -n "$FIRST" ] && days=${days%%$'
'*}
  : > $O/dropped-$m.txt
  for d in $days; do V=$E/study/root-2026-$mm-$d; rm -rf $V; mkdir -p $V; X=NONE; { [ $n -lt 8 ] || { [ $n -eq 8 ] && [ $((10#$d)) -le 11 ]; }; } && X=$EXCL
    # монета в вид суток и в ax/D20 — только если её бинлог этих суток есть (нет бинлога — в dropped-<м>.txt)
    unset HAS; declare -A HAS
    for f in $S/root/*-2026-$mm-$d.binlog; do b=${f##*/}; [[ $b =~ ^($X)-2026 ]] && continue; HAS[${b%%-2026*}]=1; ln -sf $f $V/; done
    for f in $S/root/*-2026-$mm-$d.binlog.events; do b=${f##*/}; [ -n "${HAS[${b%%-2026*}]}" ] && cp -n $f $V/; done
    AX=$E/study/ax/D20/2026-$mm-$d; rm -rf $AX; mkdir -p $AX
    for f in $E/study/approaches/D20/2026-$mm-$d/*; do b=${f##*/}; if [ $b = symbols.txt ]; then while read -r sy; do [ -n "${HAS[$sy]}" ] && echo $sy; done < $f > $AX/$b; else sy=${b%.*}; sy=${sy##*-}; [[ $sy =~ ^[A-Z0-9]+$ ]] && [ -z "${HAS[$sy]}" ] && continue; ln -s $(readlink -f $f) $AX/$b; fi; done
    for f in $S/root/verify-*.status; do b=${f##*/}; sy=${b#verify-}; sy=${sy%%.*}; if [ -n "${HAS[$sy]}" ]; then cp $f $V/; else [[ $sy =~ ^($X)$ ]] || echo "$mm-$d $sy" >> $O/dropped-$m.txt; fi; done
    cp $S/root/session.json $V/
    awk -F, 'NR==FNR{h[$1]=1;next} FNR==1||($1 in h)' <(printf '%s
' "${!HAS[@]}") $S/root/instruments.csv > $V/instruments.csv; [ -e $V/session.json ] || echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > $V/session.json
  done
  [ -n "$DRY" ] && { echo "$m DRY days=$days dropped=$(sort -u $O/dropped-$m.txt | wc -l)" >> $O/dry-summary.txt; continue; }
  python3 $A/bin/p07-all-month.py $m --merge --bin alpha-tk029-merge-v3 > $O/gen-$m.out 2>&1 || { echo "$m gen FAIL" >> $O/rest-summary.txt; continue; }
  sed -i "s#study/approaches/D20#study/ax/D20#g" $A/tmp-p07/cells-by-day/jall-$m-2026-$mm-*.sh
  rm -f $O/R$m-* $O/cached-$m-*; sync; echo 3 > /proc/sys/vm/drop_caches
  set -- $days; tot=$#
  find -L $E/study/sigma240 $E/study/klines $E/study/regime -type f 2>/dev/null | xargs -r cat > /dev/null
  ( i=0; for d in $days; do i=$((i+1)); while [ $(( $(ls $O/R$m-*.rc 2>/dev/null | wc -l) + W )) -lt $i ]; do sleep 1; done; cat $E/study/root-2026-$mm-$d/* > /dev/null; find -L $E/study/approaches/D20/2026-$mm-$d -type f 2>/dev/null | xargs -r cat > /dev/null; touch $O/cached-$m-$d; done ) & PF=$!
  t0=$(date +%s)
  echo $days | tr ' ' '\n' | xargs -P $N -I{} bash -c "while [ ! -e $O/cached-$m-{} ]; do sleep 1; done; bash $A/tmp-p07/cells-by-day/jall-$m-2026-$mm-{}.sh > $O/R$m-{}.out 2> $O/R$m-{}.err; echo \$? > $O/R$m-{}.rc"
  t1=$(date +%s); kill $PF 2>/dev/null
  echo "$m days=$tot wall=$((t1-t0))s rc: $(cat $O/R$m-*.rc | sort | uniq -c | tr '\n' ' ')" >> $O/rest-summary.txt
done
date > $O/REST_DONE
