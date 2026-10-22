#!/usr/bin/env bash
# TK-303: гейт «байт в байт»: новый pgo (tk303) против прежнего alpha-tk272d-pgo, 10 клеток F1 (B1 и ось, без новых ключей), одни сутки, md5 outputs (как tk289 gate-pgo.sh)
set -uo pipefail
G=/data/tk0303/g1; mkdir -p $G
export REASON="TK-303 гейт байт-в-байт новый pgo против прежнего" NX=1 DROP=0 SCRIPTS=/data/tk0231
bash /data/tk0267/bs-launch.sh prep $G
tail -1 $G/days.txt > $G/d1 && mv $G/d1 $G/days.txt; cat $G/days.txt
BIN=/opt/alpha-compute/bin/alpha-tk272d-pgo bash /data/tk0267/bs-launch.sh pass $G A1 2>&1 | tail -3
BIN_B=/opt/alpha-compute/bin/alpha-tk303-pgo BIN=/opt/alpha-compute/bin/alpha-tk272d-pgo bash /data/tk0267/bs-launch.sh pass $G B1 2>&1 | tail -3
wc -l $G/pass-A1/outputs.md5 $G/pass-B1/outputs.md5
if diff -q $G/pass-A1/outputs.md5 $G/pass-B1/outputs.md5; then echo "GATE: identical"; touch /data/tk0303/gate-pass.ok; else echo "GATE: DIFF"; exit 1; fi
