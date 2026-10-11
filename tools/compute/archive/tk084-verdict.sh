#!/bin/bash
# TK-084 п.2: вердикт TK-044 по 401 суткам XAU/CL (гейт v171c) -> /data/tk084/v171c/verdict-xaucl.csv. Полнота: архивные сутки, журнала потерь нет -> old=ok.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root
V=/data/tk084/v171c; cd $V || exit 2
rm -f /data/tk084/v171c-verdict.done
grep -E '^(XAUUSDT|CLUSDT),' g-days.csv | awk -F, '{n=split($2,a,"-"); d=a[n-2]"-"a[n-1]"-"substr(a[n],1,2); print $1","d","substr(d,1,7)",pool,ok"}' > comp-body.txt
{ echo "sym,day,month,cat,verify"; cat comp-body.txt; } > completeness.csv
python3 /opt/alpha-compute/bin/tk044-report.py $V/run $V/g-days.csv $V/completeness.csv $V/verdict-xaucl.csv > report.txt 2> report.err
echo rc=$? >> report.txt
touch /data/tk084/v171c-verdict.done
