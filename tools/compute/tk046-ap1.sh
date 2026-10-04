#!/bin/bash
# tk046-ap1.sh SYM DAY BINLOG: подходы D20 одним монето-суткам (бинарь alpha-e74f200-v3, как tk035-ap1.sh) -> $O/approaches/D20/<сутки>/
s=$1; d=$2; f=$3; BIN=/opt/alpha-compute/bin/alpha-e74f200-v3; O=${O:-/data/tk046/aug/study}
out=$O/approaches/D20/$d; R=$O/.roots/$d-$s
[ -s $out/approaches-$s.csv ] && exit 0
mkdir -p $R $out; ln -sf "$f" $R/
cp -n /data/alpha/root/instruments.csv $R/; echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > $R/session.json; echo ok > $R/verify-$s.status
cd $O && nice -n 19 $BIN lob touches --root $R --symbol $s --h3-mode notional --h3-usd 10000 --approach-bps 20 --out $out/touches-$s.csv > $out/$s.log 2>&1 || echo "$s $d FAIL" >> $O/failed.txt
rm -rf $R
