#!/usr/bin/env bash
# TK-486: ждёт бинарь (сборка v3 идёт отдельным юнитом) и гонит corr-bench.sh: bash corr-bench-wait.sh <бинарь> [потоки]
set -u
BIN="${1:?бинарь}"; TH="${2:-1 8}"
for _ in $(seq 1 90); do [ -x "$BIN" ] && [ "$BIN" -nt /data/tk0486/corr-bench.sh ] && break; sleep 10; done
[ -x "$BIN" ] || { echo "нет бинаря $BIN"; exit 3; }
md5sum "$BIN"; grep -m1 'model name' /proc/cpuinfo
bash /data/tk0486/corr-bench.sh "$BIN" "$TH"
