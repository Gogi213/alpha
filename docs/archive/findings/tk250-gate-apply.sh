#!/bin/bash
# TK-250 гейт П1: r1cell.py apply (янв+фев, 12 клеток) -> cmp с r1ext (фев) и r1x (янв, фев) побайтно; красное в п.1 — стоп (psim не запускается).
W=/data/tk0250; cd $W || exit 9
T0=$(date +%s)
python3 r1cell.py apply --spec gate.r1spec --pool /data/tk064/pool --t46 /data/tk046 --thr tk202-thresholds-2026-10-12.json --out out --months jan,feb > apply.log 2>&1 || { echo "APPLY_FAIL" > gate.txt; exit 2; }
T1=$(date +%s)
: > gate.txt; bad=0
while read h n; do
  if cmp -s out/cells/feb/$h.csv /data/tk189/r1ext/cells/feb/$n.csv; then echo "OK r1ext feb $h $n $(wc -l < out/cells/feb/$h.csv)" >> gate.txt; else echo "DIFF r1ext feb $h $n" >> gate.txt; bad=$((bad+1)); fi
done < map-r1ext.txt
for m in jan feb; do while read h; do
  if cmp -s out/cells/$m/$h.csv /data/tk202/r1x/cells/$m/$h.csv; then echo "OK r1x $m $h $(wc -l < out/cells/$m/$h.csv)" >> gate.txt; else echo "DIFF r1x $m $h" >> gate.txt; bad=$((bad+1)); fi
done < x-hashes.txt; done
echo "apply_s=$((T1-T0)) cmp_s=$(( $(date +%s)-T1 )) расхождений=$bad" >> gate.txt
exit $bad
