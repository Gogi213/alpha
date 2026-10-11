#!/bin/bash
export HOME=/home/deck; A=$HOME/alpha; G=$A/epochs/e-gate; J=$A/epochs/e-jan; S=/data/alpha/epochs/e-jan
EXCL="HYPEUSDT|NEARUSDT|XLMUSDT|LITUSDT|PENDLEUSDT|TRXUSDT|PUMPFUNUSDT|ATOMUSDT|JUPUSDT|WIFUSDT|DRAMUSDT"
rm -rf $G; mkdir -p $G/study $G/b5; ln -sfn $A/bin $G/bin; ln -sfn $S/root $G/root
for x in regime klines sigma240; do ln -sfn $(readlink -f $J/study/$x) $G/study/$x; done
mm=01; d=29; X=$EXCL; V=$G/study/root-2026-$mm-$d; mkdir -p $V
for f in $S/root/*-2026-$mm-$d.binlog; do b=${f##*/}; [[ $b =~ ^($X)-2026 ]] && continue; ln -sf $f $V/; done
for f in $S/root/*-2026-$mm-$d.binlog.events; do b=${f##*/}; [[ $b =~ ^($X)-2026 ]] && continue; cp -n $f $V/; done
AX=$G/study/ax/D20/2026-$mm-$d; mkdir -p $AX
for f in $J/study/approaches/D20/2026-$mm-$d/*; do b=${f##*/}; [[ $b =~ ^([a-z0-9]+-)?($X)\. ]] && continue; if [ $b = symbols.txt ]; then grep -vE "^($X)\$" $f > $AX/$b; else ln -s $(readlink -f $f) $AX/$b; fi; done
for f in $S/root/verify-*.status; do b=${f##*/}; [[ $b =~ ^verify-($X)\. ]] || cp $f $V/; done
cp $S/root/session.json $V/; grep -vE "^($X)," $S/root/instruments.csv > $V/instruments.csv
sed -e "s#study/approaches/D20#study/ax/D20#g" -e '/cells-by-day\/jall-jan-2026-01-29.grid.log/d' -e "s#cells-by-day/jall-jan-2026-01-29.extra.txt#../e-gate/extra29.txt#" $A/tmp-p07/cells-by-day/jall-jan-2026-01-29.sh > $G/cells29.sh; sed 's#study/approaches/D20#study/ax/D20#g' $A/tmp-p07/cells-by-day/jall-jan-2026-01-29.extra.txt > $G/extra29.txt
cd $G; sed -i "s#[^ ]*/../e-gate/extra29.txt#$G/extra29.txt#" cells29.sh; t0=$(date +%s); bash cells29.sh > gate.out 2> gate.err; echo "rc=$? wall=$(( $(date +%s)-t0 ))" > GATE_DONE
