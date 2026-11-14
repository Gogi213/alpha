#!/bin/bash
# Аудит закрытий соединений: три руки на биржу бок о бок на одних шардах — D (как в бою, с диагностикой), R (`--no-pong-only-degrade`), N (`--no-ping`).
# usage: audit-start.sh <секунд> — запускает 12 юнитов `audit-<биржа>-<рука>` (прекращаются сами через <секунд>), данные и события — /data/perf/audit/<биржа>-<рука>/data. Разбор — audit_report.py.
cd /data/perf
DUR=${1:-43200}; BIN=${BIN:-/data/perf/alpha-final6j}
rm -rf /data/perf/audit; mkdir -p /data/perf/audit
start() { # биржа каталог пула шарды базовые-флаги
  local ex=$1 pooldir=$2 shards=$3 base=$4
  local pool=$(ls -t $pooldir/pool-*.json | head -1)
  for arm in D R N; do
    case $arm in D) f="" ;; R) f="--no-pong-only-degrade" ;; N) f="--no-ping" ;; esac
    mkdir -p /data/perf/audit/$ex-$arm/data
    systemd-run --unit=audit-$ex-$arm --collect -p CPUAccounting=yes $BIN collect run --exchange $ex --root /data/perf/audit/$ex-$arm/data --health /data/perf/audit/$ex-$arm/health.json \
      --pool-file $pool --only-shards $shards --lanes 2 --duration-s $DUR --connect-budget 40 $base $f > /data/perf/audit/$ex-$arm/unit.txt 2>&1
  done
}
start binance /data/collect/binance/data book00,book01,mark00 "--never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel"
start bybit /data/turbo-probe/soak-bybit/data book00,book01,trade00 "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"
start okx /data/collect/okx/data book00,book01,trade00 "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"
start bingx /data/collect/bingx/data book00,book01,trade00 "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor"
date -u +%T > /data/perf/audit/started.txt
