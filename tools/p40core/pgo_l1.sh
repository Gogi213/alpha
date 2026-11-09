#!/bin/bash
# TK-515: PGO-сборки (В-32) ядра и pq2bin на ветке tk515-l1 и гейт «байт в байт» pgo против не-pgo. Запуск на srv: bash /root/p40core-tk515/pgo_l1.sh (после l1_gate.sh — нужны $T/{bn,okx}/l1).
# Ядро: те же три прогона, что pgo.sh, + прогон на L1-файлах с P40_L1=1. pq2bin: профиль на l1 book/bybit/gate и book/bybit/gate полной книги.
export PATH=$PATH:/root/.cargo/bin
Q=${Q:-/root/p40core-tk515}; T=${T:-/tmp/l1_gate}; S=${S:-/root/tk515/src}; W=/tmp/pgo_l1; LP=/root/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata
BN=${BN:-/data/stakan/chft/binance_futures/2026-09-17}; OKR=${OKR:-/root/p40ref/raw/okx}
rm -rf $W; mkdir -p $W/cd $W/pd; fail=0
A=/dev/shm/p40/A; TA=/dev/shm/p40/TA
cb() { cargo build --release "$@" 2>&1 | tail -1; }
runs() { # $1 бинарь, $2 префикс
  nice -n 19 $1 $2.ok.csv $A /dev/shm/p40/B $TA /dev/shm/p40/TB 12300 12.3 500 >/dev/null 2>&1
  P40_NOTRADE=drain nice -n 19 $1 $2.dr.csv $A /dev/shm/p40by/B $TA /dev/shm/p40by/TB 12300 12300 500 >/dev/null 2>&1
  P40_NOTRADE=skip P40_TRUNC=1 P40_VARIANTS=250:30000,500:60000 P40_THETAS=5.04,12.6 nice -n 19 $1 $2.sk.csv $A /dev/shm/p40by/B $TA /dev/shm/p40by/TB 12300 12300 500 >/dev/null 2>&1
  P40_L1=1 P40_L1_STAT=1 nice -n 19 $1 $2.l1.csv $T/bn/l1 $T/okx/l1 $TA /dev/shm/p40/TB 12300 12.3 500 >/dev/null 2>&1
}
cd $Q/rs || exit 1
RUSTFLAGS="-Cprofile-generate=$W/cd" cb --target-dir $W/cgen
runs $W/cgen/release/p40core $W/ctrain
$LP merge -o $W/cd/m.profdata $W/cd/*.profraw || exit 1
RUSTFLAGS="-Cprofile-use=$W/cd/m.profdata" cb --target-dir $W/cuse
cb
for v in base pgo; do bin=$Q/rs/target/release/p40core; [ $v = pgo ] && bin=$W/cuse/release/p40core
  ( TIMEFORMAT="ядро $v user=%U s wall=%R s"; time runs $bin $W/c_$v ) 2>&1; done
n=0; bad=0; for f in $W/c_base.*.csv*; do s=${f#$W/c_base}; n=$((n+1)); cmp -s $f $W/c_pgo$s || { bad=$((bad+1)); echo "ЯДРО РАЗНЫ: $s"; }; done
echo "ядро pgo = не-pgo: файлов $n, различий $bad"; [ $bad = 0 ] || fail=1
# pq2bin
pruns() { # $1 бинарь, $2 каталог
  P40_L1_RAW=1 $1 l1 book $BN DOGEUSDT_orderbook.parquet $2/bn 2 >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 book $OKR DOGE-USDT-SWAP_orderbook.parquet $2/okx 2 >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 bybit $S/bybit/DOGEUSDT_2026-09-17_ob200.data.zip $2/bybit >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 gate $S/gate DOGE_USDT 20260917 $2/gate 1 2 >/dev/null 2>&1
  $1 book $BN DOGEUSDT_orderbook.parquet $2/fbn 2 >/dev/null 2>&1
  $1 bybit $S/bybit/DOGEUSDT_2026-09-17_ob200.data.zip $2/fbybit >/dev/null 2>&1
  $1 gate $S/gate DOGE_USDT 20260917 $2/fgate 1 2 >/dev/null 2>&1
}
cd $Q/pq2bin || exit 1
RUSTFLAGS="-Cprofile-generate=$W/pd" cb --target-dir $W/pgen
pruns $W/pgen/release/pq2bin $W/ptrain
$LP merge -o $W/pd/m.profdata $W/pd/*.profraw || exit 1
RUSTFLAGS="-Cprofile-use=$W/pd/m.profdata" cb --target-dir $W/puse
cb
for v in base pgo; do bin=$Q/pq2bin/target/release/pq2bin; [ $v = pgo ] && bin=$W/puse/release/pq2bin
  ( TIMEFORMAT="pq2bin $v user=%U s wall=%R s"; time pruns $bin $W/p_$v ) 2>&1; done
n=0; bad=0; for f in $(cd $W/p_base && find . -type f ! -name '*.zst' | sort); do n=$((n+1)); cmp -s $W/p_base/$f $W/p_pgo/$f || { bad=$((bad+1)); echo "PQ2BIN РАЗНЫ: $f"; }; done
echo "pq2bin pgo = не-pgo: файлов $n, различий $bad"; [ $bad = 0 ] || fail=1
echo "PGO_L1 КОНЕЦ fail=$fail"
exit $fail
