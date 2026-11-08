#!/bin/bash
# TK-500: PGO-сборка ядра (В-32) и гейт «байт в байт» против не-PGO. Запуск на 5.231.25.224: bash /root/p40core/pgo.sh
# Профиль снимается на трёх прогонах, покрывающих пути §18 (OKX со сделками; Bybit без сделок drain; Bybit skip + усечение + списки θ и вариантов);
# гейт — те же три прогона без PGO и с PGO, cmp по всем таблицам. Нужны /dev/shm/p40/{A,TA,B,TB} и /dev/shm/p40by/{B,TB} (srv-notrade-smoke.sh).
export PATH=$PATH:/root/.cargo/bin
cd /root/p40core/rs || exit 1
A=/dev/shm/p40/A; TA=/dev/shm/p40/TA
runs() { # $1 = бинарь, $2 = префикс выходов
  nice -n 19 $1 $2.ok.csv $A /dev/shm/p40/B $TA /dev/shm/p40/TB 12300 12.3 500 >/dev/null 2>&1
  P40_NOTRADE=drain nice -n 19 $1 $2.dr.csv $A /dev/shm/p40by/B $TA /dev/shm/p40by/TB 12300 12300 500 >/dev/null 2>&1
  P40_NOTRADE=skip P40_TRUNC=1 P40_VARIANTS=250:30000,500:60000 P40_THETAS=5.04,12.6 nice -n 19 $1 $2.sk.csv $A /dev/shm/p40by/B $TA /dev/shm/p40by/TB 12300 12300 500 >/dev/null 2>&1
}
rm -rf /tmp/pgo-data /tmp/pgo-gen /tmp/pgo-use /tmp/pgo_*; mkdir -p /tmp/pgo-data
RUSTFLAGS="-Cprofile-generate=/tmp/pgo-data" cargo build --release --target-dir /tmp/pgo-gen 2>&1 | tail -1
runs /tmp/pgo-gen/release/p40core /tmp/pgo_train
/root/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata merge -o /tmp/pgo-data/merged.profdata /tmp/pgo-data/*.profraw || exit 1
RUSTFLAGS="-Cprofile-use=/tmp/pgo-data/merged.profdata" cargo build --release --target-dir /tmp/pgo-use 2>&1 | tail -1
cargo build --release 2>&1 | tail -1
for v in base pgo; do
  bin=./target/release/p40core; [ $v = pgo ] && bin=/tmp/pgo-use/release/p40core
  for i in 1 2 3; do
    ( TIMEFORMAT="$v run$i user=%U s wall=%R s"; time runs $bin /tmp/pgo_$v ) 2>&1
  done
done
n=0; bad=0
for p in ok dr sk; do for f in /tmp/pgo_base.$p.csv*; do
  s=${f#/tmp/pgo_base}; n=$((n+1)); cmp -s $f /tmp/pgo_pgo$s || { bad=$((bad+1)); echo "РАЗНЫ: $s"; }
done; done
echo "байт в байт: файлов $n, различий $bad"
