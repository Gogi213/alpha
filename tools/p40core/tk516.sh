#!/bin/bash
# TK-516: сборка, тесты, сверка «байт в байт» и замер pq2bin на srv. Запуск с ПК: python tools/p40core/tk516_run.py <build|test|ab|bench|all>
# База — замороженная копия прежнего (TK-515) pq2bin: /root/tk516/base-pq2bin. Замер — user-CPU секунды (srv загружен, wall не годится), МБ сырья на ядро (1 поток).
export PATH=$PATH:/root/.cargo/bin
Q=${Q:-/root/p40core-tk516}; N=$Q/pq2bin/target/release/pq2bin; B=/root/tk516/base-pq2bin
S=${S:-/root/tk515/src}; BN=${BN:-/data/stakan/chft/binance_futures/2026-09-17}; OK=${OK:-/root/p40ref/raw/okx}; T=${T:-/tmp/tk516}
Z=$S/bybit/DOGEUSDT_2026-09-17_ob200.data.zip
mkdir -p /root/tk516; [ -f $B ] || cp /root/p40core-tk515/pq2bin/target/release/pq2bin $B
runs() { # бинарь, каталог, потоков book/gate
  P40_L1_RAW=1 $1 l1 book $BN DOGEUSDT_orderbook.parquet $2/bn $3 >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $2/okx $3 >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 bybit $Z $2/bybit >/dev/null 2>&1
  P40_L1_RAW=1 $1 l1 gate $S/gate DOGE_USDT 20260917 $2/gate 1 $3 >/dev/null 2>&1
  $1 book $BN DOGEUSDT_orderbook.parquet $2/fbn $3 >/dev/null 2>&1
  $1 bybit $Z $2/fbybit >/dev/null 2>&1
  $1 gate $S/gate DOGE_USDT 20260917 $2/fgate 1 $3 >/dev/null 2>&1
}
fail=0
case $1 in
build) cd $Q/pq2bin && touch src/*.rs Cargo.toml && nice -n 5 cargo build --release 2>&1 | tail -15; [ -x $N ] || fail=1 ;;
test) cd $Q/pq2bin && nice -n 5 cargo test --release 2>&1 | tail -30 | tee /tmp/tk516-test.out; grep -q "test result: ok" /tmp/tk516-test.out && ! grep -q "FAILED\|error" /tmp/tk516-test.out || fail=1 ;;
ab)
  rm -rf $T/ab; mkdir -p $T/ab/base $T/ab/new
  runs $B $T/ab/base 2; runs $N $T/ab/new 2
  nb=$(find $T/ab/base -type f | wc -l); nn=$(find $T/ab/new -type f | wc -l); sz=$(du -sb $T/ab/new | cut -f1)
  if diff -rq $T/ab/base $T/ab/new && [ "$nb" = "$nn" ] && [ "$nb" -gt 100 ]; then echo "AB: файлов $nb = $nn, байт $sz, различий 0"; else echo "AB: РАЗЛИЧИЯ или файлов $nb/$nn"; fail=1; fi
  # граничный вход: усечённый zip-поток и пустой час — те же ошибки/коды у обеих версий не проверяем (только штатные входы)
  ;;
bench)
  raw_bn=$(cat $BN/*/DOGEUSDT_orderbook.parquet | wc -c); raw_ok=$(cat $OK/*/DOGE-USDT-SWAP_orderbook.parquet | wc -c)
  raw_by=$(stat -c %s $Z); raw_ga=$(cat $S/gate/DOGE_USDT-20260917*.csv.gz | wc -c)
  TIMEFORMAT=%U
  m() { # имя бинарь сырьё-байт команда...
    local n=$1 b=$2 raw=$3; shift 3; local u; u=$( { time P40_L1_RAW=0 "$@" >/dev/null 2>&1; } 2>&1 )
    awk -v n="$n" -v b="$b" -v raw="$raw" -v u="$u" 'BEGIN{printf "%-6s %-5s user %6.2f с  %6.2f МБ/с на ядро\n", n, b, u, raw/1e6/u}'
  }
  for rep in 1 2; do for v in base new; do bin=$B; [ $v = new ] && bin=$N; D=$T/bench/$v; rm -rf $D
    m bn $v $raw_bn $bin l1 book $BN DOGEUSDT_orderbook.parquet $D/bn 1
    m okx $v $raw_ok $bin l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $D/okx 1
    m bybit $v $raw_by $bin l1 bybit $Z $D/bybit
    m gate $v $raw_ga $bin l1 gate $S/gate DOGE_USDT 20260917 $D/gate 1 1
  done; done ;;
ins) # instructions:u и task-clock (не зависят от загрузки srv): base против new, один поток
  rm -rf $T/ins; mkdir -p $T/ins
  st() { local n=$1 b=$2; shift 2
    P40_L1_RAW=0 perf stat -x, -e instructions:u,task-clock "$@" 2>&1 >/dev/null | awk -F, -v n=$n -v b=$b '/instructions/{i=$1} /task-clock/{t=$1} END{printf "%-6s %-5s instr %8.2f G  task %7.2f s\n", n, b, i/1e9, t/1e3}'; }
  for v in base new; do bin=$B; [ $v = new ] && bin=$N; D=$T/ins/$v
    st bn $v $bin l1 book $BN DOGEUSDT_orderbook.parquet $D/bn 1
    st okx $v $bin l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $D/okx 1
    st bybit $v $bin l1 bybit $Z $D/bybit
    st gate $v $bin l1 gate $S/gate DOGE_USDT 20260917 $D/gate 1 1
  done ;;
*) echo "build|test|ab|bench|ins"; fail=2 ;;
esac
exit $fail
