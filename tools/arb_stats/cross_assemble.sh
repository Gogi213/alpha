#!/bin/bash
# Сборка каталогов полного окна для cross_run.py: дочитать хвост книг и сделок в СВОИ каталоги (общие live/w2 не трогаем), собрать жёсткие ссылки,
# определить t1 (минимум по биржам последней записи − 2 мин) и пропуски сбора. Запуск: bash cross_assemble.sh (юнитом systemd-run).
set -euo pipefail
W=/data/arb-cross
T=/opt/arb-cross/tools/arb_stats
cd $T
mkdir -p $W/tailb $W/tailw
[ -d $W/tailb/state ] || cp -a /data/arb-tab/live/state $W/tailb/state
[ -d $W/tailw/state ] || cp -a /data/arb-maker/w2/state $W/tailw/state
echo "tail books"; python3 -c "import arb_live,time; print(arb_live.parse_all('$W/tailb', int(time.time()*1000), 3))"
echo "tail trades"; python3 maker_parse.py $W/tailw 3
for ex in binance bybit okx bingx; do
  mkdir -p $W/full/b/parsed/$ex $W/full/w/parsed/$ex
  for src in /data/arb-maker/old/b/parsed /data/arb-tab/live/parsed $W/tailb/parsed; do
    [ -d $src/$ex ] && cp -al --no-clobber $src/$ex/. $W/full/b/parsed/$ex/ || true
  done
  for src in /data/arb-maker/old/w/parsed /data/arb-maker/w2/parsed $W/tailw/parsed; do
    [ -d $src/$ex ] && cp -al --no-clobber $src/$ex/. $W/full/w/parsed/$ex/ || true
  done
done
python3 - <<'EOF'
import glob, json, sys
import numpy as np
sys.path.insert(0, "/opt/arb-cross/tools/arb_stats")
from common import EXCHANGES
from arb_live import symmap_for
W = "/data/arb-cross/full"
sm, _ = symmap_for(__import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()))
last = {}
for ex in EXCHANGES:
    syms = [s for s, (c, m) in sm[ex].items() if c in ("BTC", "ETH", "SOL", "XRP", "DOGE") and m == 1.0]
    mx = 0
    for s in syms:
        for f in glob.glob(f"{W}/b/parsed/{ex}/{s}.*.npz"):
            mx = max(mx, int(np.load(f)["e"].max()))
    last[ex] = mx
    print(ex, "last book e", mx)
t1 = (min(last.values()) - 120_000) // 60_000 * 60_000
t0 = 1794550440000
json.dump({"t0": t0, "t1": t1, "last": last}, open("/data/arb-cross/window.json", "w"))
print("window", t0, t1, (t1 - t0) / 3.6e6, "h")
EOF
python3 gaps_scan.py $W/full/b/parsed $(python3 -c "import json;d=json.load(open('$W/window.json'));print(d['t0'],d['t1'])") $W/full/dead.json 15 > $W/full/gaps.txt
cat $W/full/gaps.txt
echo DONE
