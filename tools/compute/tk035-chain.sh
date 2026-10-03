#!/bin/bash
# chain.sh: ждёт VF_DONE, гейт по verify, ref.py по 1 суткам на монету, подмена, манифест
cd /root/tk035new; L=chain.log
while [ ! -e VF_DONE ]; do sleep 10; done
bad=0
for s in ATOMUSDT DRAMUSDT JUPUSDT LITUSDT NEARUSDT PENDLEUSDT PUMPFUNUSDT TRXUSDT WIFUSDT XLMUSDT; do
  st=$(grep -c "status=ok" vf-$s.log); rc1=$(grep -c "^rc=0" vf-$s.log); rc2=$(grep -c "^rc=0" vfk-$s.log)
  br=$(grep -E "broken_updates=[1-9]" vfk-$s.log | wc -l); ex=$(grep -c " example " vfk-$s.log)
  nf=$(ls $s/*.binlog | wc -l)
  echo "GATE $s files=$nf status_ok=$st rc_default=$rc1 rc_keepgoing=$rc2 parts_broken=$br examples=$ex" >> $L
  { [ "$st" = 1 ] && [ "$rc1" = 1 ] && [ "$rc2" = 1 ] && [ "$br" = 0 ] && [ "$ex" = 0 ]; } || bad=1
done
if [ $bad = 1 ]; then echo "GATE_FAIL — подмена не выполнена" >> $L; touch CHAIN_FAIL; exit 1; fi
echo "GATE_OK $(date -u +%T)Z" >> $L
for s in ATOMUSDT DRAMUSDT JUPUSDT LITUSDT NEARUSDT PENDLEUSDT PUMPFUNUSDT TRXUSDT WIFUSDT XLMUSDT; do
  f=$(ls tk035raw10-link 2>/dev/null; ls /root/tk035raw10/ob-$s-*.zip | sed -n 100p); d=${f##*ob-$s-}; d=${d%.zip}
  echo "REF $s $d: $(unzip -p $f | python3 /data/tk035/ref.py | tr "\n" " " | cut -c1-260)" >> $L
done
ls /root/tk035new/*USDT/*.binlog /root/tk035new/hype/*.binlog > swap-list.txt
wc -l < swap-list.txt >> $L
mkdir -p /data/alpha-oldtick
xargs -a swap-list.txt -P 3 -n1 ./swap1.sh > swap.log 2>&1
echo "SWAP ok=$(grep -c ^OK swap.log) fail=$(grep -vc ^OK swap.log) $(date -u +%T)Z" >> $L
cat swap-manifest.part.* > /data/alpha/epochs/manifests/tk035-reimport-$(date -u +%Y%m%dT%H%M%S).sha256
touch SWAP_DONE
