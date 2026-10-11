#!/bin/bash
# TK-194: g95-sw4/sw5 на R2-B с равной экспозицией §6(2) (без пересчёта бэктеста, В-213): деревья суток -> tk194-g95-expo.py -> portfolio-sim cap 0 и 3
# (--drop TRUMP/TRX/BCH, как tk190-psim.sh) -> /data/tk0194/closes-r2bg95-cap<N>-<мес>.json. Основа m (строка B1 того же сигнала) — s-b (сырые деревья B1, в R2-B своих базовых строк нет); сделки клеток: янв–июл r-b, авг–сен r-bf (3 досчитанных символо-суток, TK-190; у B1 их нет — сделки без базы пропускаются и считаются в base_missing). Метка expo.done.
set -u
O=/data/tk0194; P=/data/p12r3a; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1
cd $O || exit 2; rm -f expo.done fail.txt; rm -rf s-x r-x g95-b; mkdir s-x r-x
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07; do ln -s $P/s-b/$mo s-x/$mo; ln -s $P/r-b/$mo r-x/$mo; done
for mo in 2026-08 2026-09; do ln -s $P/s-b/$mo s-x/$mo; ln -s $P/r-bf/$mo r-x/$mo; done
EXPO_DROP=$DROP python3 $O/tk194-g95-expo.py s-x r-x g95-b > expo.log 2>&1 || { echo FAILEXPO >> fail.txt; exit 1; }
B=ladder3x0..0.0409; args=()
for n in 4 5; do f=${B}sw$n-pct2-tr1x1-14400-ttl1800; args+=(--variant "$S@$f=$S/$f"); done
for mo in $(ls g95-b | grep "^20" | sort); do
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/g95-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r2bg95-cap$cap-$mo.json > psim-r2bg95-cap$cap-$mo.txt 2> psim-r2bg95-cap$cap-$mo.log || echo "FAIL $cap $mo" >> fail.txt
  done
done
touch expo.done
