#!/bin/bash
# TK-436: гейт «было/стало» K2 (OI у пика): tk433-oi.py (разбор csv.gz) против tk436-vec.py oi (срез куба 5 мин) на v10 e15 сентября; строки «%.6g» сверяются
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
E=/data/tk433/e15
rm -rf oigate; mkdir oigate; rm -f oigate.rc
s=$(date +%s.%N)
$PY tk433-oi.py $E/end-start-th15.csv $E/trades-th15.csv /srv/alpha-sda/tk415/out oigate/old.csv > oigate/old.log 2>&1
m=$(date +%s.%N)
$PY tk436-vec.py oi $E/end-start-th15.csv $E/trades-oi-th15.csv cube3 oigate/new.csv > oigate/new.log 2>&1
rc=$?
e=$(date +%s.%N)
echo "old $(echo "$m - $s" | bc) s; new $(echo "$e - $m" | bc) s; vec rc $rc" > oigate/time.txt
# прежний выход (K2 из старого прогона) и свежий «было» — одно и то же?
$PY - <<'PY' >> oigate/time.txt
import csv
a={r["id"]:(r["K2_pre"],r["K2_post"]) for r in csv.DictReader(open("/data/tk433/e15/trades-oi-th15.csv"))}
b={r["id"]:(r["K2_pre"],r["K2_post"]) for r in csv.DictReader(open("oigate/old.csv"))}
print("старый прогон == свежий «было»:", a==b, len(a), len(b))
PY
echo $rc > oigate.rc
