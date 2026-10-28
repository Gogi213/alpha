#!/bin/bash
# TK-436 (return 3, п.2): сквозная итерация v11f на КУБЕ, сентябрь, 15 процессов, стена по шагам в t.tsv:
#   контроли (tk433-features windows --cube --cut --jobs 15) -> tk422-features (--cube --cube-trades --procs 15) -> merge.
# Ждёт гейт tk422 (/data/tk0436/f422gate/time.txt == IDENTICAL), иначе выход 3. «Было» v11f: 13:19 (5:32 + 7:42 + 0:04); v11 на кубе: 7:18 (v11cube).
#   alsched submit --kind prod --name tk436-e2e --cores 15 --max-runtime 40m -- bash /data/tk0436/tk436-e2e.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
G=${G:-/data/tk0436/f422gate/time.txt}
for i in $(seq 1 240); do grep -qE '^(IDENTICAL|DIFF)' $G 2>/dev/null && break; sleep 15; done
grep -q '^IDENTICAL' $G 2>/dev/null || { echo 3 > /data/tk0436/e2e.rc; exit 3; }
O=/data/tk0436/e2e
rm -rf $O; mkdir -p $O; rm -f /data/tk0436/e2e.rc
T0=$(date +%s.%N); t() { echo -e "$1\t$(echo "$(date +%s.%N) - $T0" | bc)" >> $O/t.tsv; }
$PY /data/tk0436/tk433-features.py windows smp/legs-2026-09.csv smp/legs-2026-09.csv - - $O 2026-09 --cube /data/tk0436/cube3 --cut 29759040 --jobs 15 > $O/windows.log 2>&1 || { echo 1 > /data/tk0436/e2e.rc; exit 1; }
t controls
$PY /data/tk0436/tk422-features.py $O/windows.csv $O/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/feat.log 2> $O/feat.err || { echo 2 > /data/tk0436/e2e.rc; exit 2; }
t tk422
$PY /data/tk0436/tk433-features.py merge $O/windows.csv $O/own.csv $O/tk422-features.csv $O/features-2026-09.csv > $O/merge.log 2>&1 || { echo 4 > /data/tk0436/e2e.rc; exit 4; }
t merge
ok=1
for f in windows.csv own.csv controls-diag.json tk422-features.csv; do
  cmp f-2026-09/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/cmp.txt || { echo "DIFF $f" >> $O/cmp.txt; ok=0; }
done
cmp g-2026-09/features-2026-09.csv $O/features-2026-09.csv > /dev/null 2>&1 && echo "OK features-2026-09.csv" >> $O/cmp.txt || { echo "DIFF features-2026-09.csv" >> $O/cmp.txt; ok=0; }
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0436/e2e.rc
