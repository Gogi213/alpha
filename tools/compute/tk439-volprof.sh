#!/bin/bash
cd /data/tk0435 || exit 9
X=$(/data/tk421/venv/bin/python3 -c "import json;print(json.load(open('/data/tk0436/v11cube/xq.json'))['90|+']['q99'])")
/data/tk421/venv/bin/python3 /data/tk0439/tk439-volprof.py /data/tk0436/v11cube/ev/windows-2026-09.csv 90 $X /data/tk0436/cube3 29759040 40 > /data/tk0439/volprof.txt 2>&1
echo 0 > /data/tk0439/vp.rc
