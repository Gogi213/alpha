#!/bin/bash
# TK-441 круг 3: запись вызовов corr/fractals из new.py на UAIUSDT (298 окон, 1 процесс) + микро-замер numba-способов (tk441-arrbench.py).
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 15m --name tk441-arr -- bash /data/tk0441/tk441-arr.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
NPY=/data/tk0439/venv/bin/python
O=/data/tk0441/arr
rm -rf $O; mkdir -p $O
cp /data/tk0439/f422prof2/win.csv $O/win.csv
$PY - <<'PYEOF'
s = open("/data/tk0441/new.py").read()
s = s.replace("def corr(x, y):\n    n = len(x)", "def corr(x, y):\n    _RC.append((x, y)) if len(_RC) < 100000 else None\n    n = len(x)", 1)
s = s.replace("    xs = [b[1][j] for b in bars]\n", "    xs = [b[1][j] for b in bars]\n    _RF.append((xs, k, hi)) if len(_RF) < 100000 else None\n", 1)
s = s.replace("import csv, gzip", "import atexit, pickle\n_RC = []; _RF = []\natexit.register(lambda: pickle.dump({'corr': _RC, 'fr': _RF}, open('/data/tk0441/arr/rec.pkl', 'wb')))\nimport csv, gzip", 1)
open("/data/tk0441/arr/rec.py", "w").write(s)
PYEOF
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
$PY $O/rec.py $O/win.csv $O/rec.csv $A > $O/rec.log 2> $O/rec.err
NUMBA_CACHE_DIR=/data/tk0439/nbcache $NPY /data/tk0441/tk441-arrbench.py $O/rec.pkl > $O/bench.txt 2>> $O/rec.err
echo 0 > $O/done
