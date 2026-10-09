#!/bin/bash
# imp10.sh SYM DAY: импорт --steps-from-snapshot из /root/tk035raw10 в /root/tk035new/<SYM>
R=/root/tk035raw10; s=$1; d=$2
./imp.sh $s $d $R/ob-$s-$d.zip $R/tr-$s-$d.csv.gz /root/tk035new/$s --steps-from-snapshot 2>&1 | sed "s/^/$s $d /"
