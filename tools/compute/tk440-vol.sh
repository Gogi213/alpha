#!/bin/bash
# TK-440 шаг 4: меры вол. (tk435-vol, 22 варианта pre/in + контроли) на кубах свечей янв–июль, 5 D x 2 знака, X_D = q99 сентября (xq.json). Метка vol.done, код vol.rc.
#   alsched submit --kind prod --name tk440-vol --cores 15 --max-runtime 150m --est 70m -- bash /data/tk0440/tk440-vol.sh
cd /data/tk0440 || exit 9
PY=/data/tk421/venv/bin/python3
V=/data/tk0433/v11
rm -f vol.done vol.rc; mkdir -p vol
for M in 01 02 03 04 05 06 07; do
  for D in 90 180 360 720 1440; do
    for S in + -; do
      X=$($PY -c "import json;print(json.load(open('$V/xq.json'))['$D|$S']['q99'])")
      N=$([ "$S" = "+" ] && echo p || echo m)
      [ -s vol/vol-$M-D$D$N.csv ] && continue
      $PY $V/tk435-vol.py ev$M/windows-2026-$M.csv $D $X vol/vol-$M-D$D$N.csv --cube cube$M --procs 15 > vol/vol-$M-D$D$N.log 2>&1 || { echo "$M $D $S" > vol.rc; exit 1; }
    done
  done
done
echo 0 > vol.rc; touch vol.done
