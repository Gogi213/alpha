#!/usr/bin/env bash
# TK-289: гейт «байт в байт»: новый pgo (tk0289c, ttl F3) против эталона g1 (A1 = tk279-pgo, прежние значения), 10 клеток F1, одни сутки
set -uo pipefail
G=/data/tk0289/g1
export REASON="TK-289 гейт байт-в-байт новый pgo (ttl F3) против эталона g1" NX=3 DROP=0 SCRIPTS=/data/tk0231
BIN_B=/opt/alpha-compute/bin/alpha-tk0289c-pgo BIN=/opt/alpha-compute/bin/alpha-tk279-pgo bash /data/tk0267/bs-launch.sh pass $G B4 2>&1 | tail -3
wc -l $G/pass-A1/outputs.md5 $G/pass-B4/outputs.md5
if diff -q $G/pass-A1/outputs.md5 $G/pass-B4/outputs.md5; then echo "GATE: identical"; touch /data/tk0289/gate4.ok; else echo "GATE: DIFF"; exit 1; fi
