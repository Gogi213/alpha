#!/usr/bin/env bash
cd /data/p42-tk598/py
P=/data/tk592/venv/bin/python
T=/data/p42-tk598/tab
rc=0
for s in long:L short:S; do side=${s%%:*}; v=${s##*:}
  echo "$(date +%T) fit $side" >> /data/p42-tk598/fit/fit.log
  $P tk598_fit.py fit --side $side --u0 $T/tab-F-${v}_u0.parquet --u1 $T/tab-F-${v}_u1.parquet --out /data/p42-tk598/fit --procs 6 >> /data/p42-tk598/fit/fit.log 2>&1 || rc=$?
done
echo $rc > /data/p42-tk598/fit/DONE
