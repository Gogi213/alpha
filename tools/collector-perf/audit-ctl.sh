#!/bin/bash
# Контроль к аудиту (14.11): две руки C1/C2 на биржу — боевой бинарь (Binance — final6i, остальные — final6h), те же шарды и флаги, что у руки D (final6j с боевым правилом).
# Сравнение D с C1 (тождество `collect compare`, ЦП, события) при шуме C1 против C2 — условие накатa final6j в бой.
# usage: audit-ctl.sh <секунд>; затем audit-cpu.sh снимает ЦП и счётчики.
cd /data/perf
DUR=${1:-7200}
start() { # биржа каталог пула шарды бинарь базовые-флаги
  local ex=$1 pooldir=$2 shards=$3 bin=$4 base=$5
  local pool=$(ls -t $pooldir/pool-*.json | head -1)
  for arm in C1 C2; do
    mkdir -p /data/perf/audit/$ex-$arm/data
    systemd-run --unit=audit-$ex-$arm --collect -p CPUAccounting=yes $bin collect run --exchange $ex --root /data/perf/audit/$ex-$arm/data --health /data/perf/audit/$ex-$arm/health.json \
      --pool-file $pool --only-shards $shards --lanes 2 --duration-s $DUR --connect-budget 40 $base > /data/perf/audit/$ex-$arm/unit.txt 2>&1
  done
}
start binance /data/collect/binance/data book00,book01,mark00 /data/perf/alpha-final6i "--never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel"
start bybit /data/turbo-probe/soak-bybit/data book00,book01,trade00 /data/perf/alpha-final6h "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"
start okx /data/collect/okx/data book00,book01,trade00 /data/perf/alpha-final6h "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"
start bingx /data/collect/bingx/data book00,book01,trade00 /data/perf/alpha-final6h "--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor"
date -u +%T > /data/perf/audit/ctl-started.txt
