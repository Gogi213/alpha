#!/bin/bash
# TK-470 гейт: C-X тем же путём драйвера (CTL=CX) на 3 парах против текущего d2b-out байт в байт; C1/C2 на тех же парах — строки и охват.
G=/data/tk0470/gate; PY=/data/tk421/venv/bin/python; rm -rf $G; mkdir -p $G
printf "0GUSDT 60\nASTERUSDT 60\nBMTUSDT 60\n" > $G/plan3.txt
CTL=CX NX=3 OUT=$G/x bash /data/tk0470/tk470-c12-run.sh $G/plan3.txt > $G/x.log 2>&1
for s in 0GUSDT ASTERUSDT BMTUSDT; do
  cmp <(zcat $G/x/cx/$s-60.csv.gz) <(zcat /data/tk0461/d2b-out/$s-60.csv.gz) >/dev/null && v=BYTES_EQ || v=DIFF
  echo "$s CX: $v" >> $G/gate.log
done
NX=3 OUT=$G/c bash /data/tk0470/tk470-c12-run.sh $G/plan3.txt > $G/c.log 2>&1
for r in c1 c2; do for s in 0GUSDT ASTERUSDT BMTUSDT; do echo "$s $r: строк $(zcat $G/c/$r/$s-60.csv.gz | wc -l), c=$(zcat $G/c/$r/$s-60.csv.gz | $PY -c 'import sys,pandas as pd;d=pd.read_csv(sys.stdin);print(int(d.c.sum()) if len(d) else 0)')" >> $G/gate.log; done; done
cat $G/x/DONE $G/c/DONE >> $G/gate.log; echo end > $G/gate.DONE
