#!/bin/bash
# Сборка каталогов полного окна для tokyo_run.py: продолжает разбор с состояния cross_assemble.sh (копия состояния в /data/arb-tokyo, /data/arb-cross не трогаем),
# жёсткие ссылки на уже разобранное, хвост книг и сделок до «сейчас», t1 = минимум по биржам последней записи − 2 мин, пропуски сбора.
# Запуск юнитом systemd-run: bash tokyo_assemble.sh
set -euo pipefail
W=/data/arb-tokyo
S=/data/arb-cross
T=/opt/arb-tokyo/tools/arb_stats
cd $T
mkdir -p $W/tailb $W/tailw $W/full/b/parsed $W/full/w/parsed
[ -d $W/tailb/state ] || cp -a $S/tailb/state $W/tailb/state
[ -d $W/tailw/state ] || cp -a $S/tailw/state $W/tailw/state
mkdir -p $W/tailb/parsed $W/tailw/parsed
for ex in binance bybit okx bingx; do
  [ -d $S/tailb/parsed/$ex ] && { mkdir -p $W/tailb/parsed/$ex; cp -al --no-clobber $S/tailb/parsed/$ex/. $W/tailb/parsed/$ex/; } || true
  [ -d $S/tailw/parsed/$ex ] && { mkdir -p $W/tailw/parsed/$ex; cp -al --no-clobber $S/tailw/parsed/$ex/. $W/tailw/parsed/$ex/; } || true
done
echo "tail books"; python3 -c "import arb_live,time; print(arb_live.parse_all('$W/tailb', int(time.time()*1000), 3))"
echo "tail trades"; python3 maker_parse.py $W/tailw 3
for ex in binance bybit okx bingx; do
  mkdir -p $W/full/b/parsed/$ex $W/full/w/parsed/$ex
  for src in $S/full/b/parsed $W/tailb/parsed; do
    [ -d $src/$ex ] && cp -al --no-clobber $src/$ex/. $W/full/b/parsed/$ex/ || true
  done
  for src in $S/full/w/parsed $W/tailw/parsed; do
    [ -d $src/$ex ] && cp -al --no-clobber $src/$ex/. $W/full/w/parsed/$ex/ || true
  done
done
python3 - <<'PY'
import glob, json, sys
import numpy as np
sys.path.insert(0, "/opt/arb-tokyo/tools/arb_stats")
from common import EXCHANGES
from gaps_scan import liquid
W = "/data/arb-tokyo/full"
last = {}
for ex in EXCHANGES:
    syms = liquid(W + "/b/parsed", ex)
    mx = 0
    for s in syms:
        for f in glob.glob(f"{W}/b/parsed/{ex}/{s}.*.npz"):
            mx = max(mx, int(np.load(f)["e"].max()))
    last[ex] = mx
    print(ex, "last book e", mx)
t1 = (min(last.values()) - 120_000) // 60_000 * 60_000
t0 = 1794550440000
json.dump({"t0": t0, "t1": t1, "last": last}, open("/data/arb-tokyo/window.json", "w"))
print("window", t0, t1, (t1 - t0) / 3.6e6, "h")
PY
python3 gaps_scan.py $W/full/b/parsed $(python3 -c "import json;d=json.load(open('/data/arb-tokyo/window.json'));print(d['t0'],d['t1'])") $W/full/dead.json 15 > $W/full/gaps.txt
cat $W/full/gaps.txt
echo DONE
