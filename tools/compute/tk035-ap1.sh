#!/bin/bash
# ap1.sh SYM DAY [BIN] [OUTBASE]: подходы D20 (touches --approach-bps 20) по файлу эпохи
# Бинарник alpha-e74f200-v3: approaches/touches/mids1m == кэш tk015 байт в байт (гейт 03.10, DOGE/AAVE 05-02); tk029-merge-v3 даёт иной approaches.
s=$1; d=$2; BIN=${3:-/opt/alpha-compute/bin/alpha-e74f200-v3
case ${d:5:2} in 01) m=jan;; 02) m=feb;; 03) m=mar;; 04) m=apr;; 05) m=may;; 06) m=jun;; 07) m=jul;; 08) m=aug;; esac
R=/root/tk035ap/root/$m-$d; mkdir -p $R $O/$d
ln -sf /data/alpha/epochs/e-$m/root/$s-$d.binlog $R/
cp -n /data/alpha/root/instruments.csv $R/; echo "{\"start_hour_utc\":0,\"closed\":true,\"binlog_files\":[]}" > $R/session.json; echo ok > $R/verify-$s.status
cd /root/tk035ap && nice -n 19 $BIN lob touches --root $R --symbol $s --h3-mode notional --h3-usd 10000 --approach-bps 20 --out $O/$d/touches-$s.csv > $O/$d/$s.log 2>&1 || echo "$s $d FAIL" >> $O/failed.txt
