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
test) cd $Q/pq2bin && touch src/*.rs Cargo.toml && nice -n 5 timeout 900 cargo test --release 2>&1 | tail -30 | tee /tmp/tk516-test.out; grep -q "test result: ok" /tmp/tk516-test.out && ! grep -q "FAILED\|error" /tmp/tk516-test.out || fail=1 ;;
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
    P40_L1_RAW=0 perf stat -x, -e instructions:u,cycles:u,task-clock "$@" 2>&1 >/dev/null | awk -F, -v n=$n -v b=$b '/instructions/{i=$1} /cycles/{c=$1} /task-clock/{t=$1} END{printf "%-6s %-5s instr %8.2f G  cycles %8.2f G  task %7.2f s\n", n, b, i/1e9, c/1e9, t/1e3}'; }
  for v in base new; do bin=$B; [ $v = new ] && bin=$N; D=$T/ins/$v
    st bn $v $bin l1 book $BN DOGEUSDT_orderbook.parquet $D/bn 1
    st okx $v $bin l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $D/okx 1
    st bybit $v $bin l1 bybit $Z $D/bybit
    st gate $v $bin l1 gate $S/gate DOGE_USDT 20260917 $D/gate 1 1
  done ;;
zl) # уровни zstd выхода l1 (после ab: эталон — несжатые .l1.bin из $T/ab/new): циклы, размер .zst, равенство после распаковки
  for L in ${LV:--3 1 2 3}; do for src in bn okx bybit gate; do D=$T/zl/$L/$src; rm -rf $D; mkdir -p $T/zl/$L
    case $src in
      bn) a="book $BN DOGEUSDT_orderbook.parquet $D 1" ;;
      okx) a="book $OK DOGE-USDT-SWAP_orderbook.parquet $D 1" ;;
      bybit) a="bybit $Z $D" ;;
      gate) a="gate $S/gate DOGE_USDT 20260917 $D 1 1" ;;
    esac
    c=$(P40_ZSTD=$L P40_L1_RAW=0 perf stat -x, -e cycles:u $N l1 $a 2>&1 >/dev/null | awk -F, '/cycles/{printf "%.2f", $1/1e9}')
    bad=0; sz=0; for f in $D/*.l1.bin.zst; do r=$T/ab/new/$src/$(basename $f .zst); zstd -dc $f | cmp -s - $r || bad=$((bad+1)); sz=$((sz+$(stat -c %s $f))); done
    echo "zstd=$L $src cycles ${c}G  zst-байт $sz  расхождений-после-распаковки $bad"
  done; done ;;
prof) # perf record cycles:u по источнику, self % по символам -> $T/prof/<src>.txt (корзины считает tk516_buckets.py); R=повторов не нужен: разбивка, не скорость
  rm -rf $T/prof; mkdir -p $T/prof
  pr() { local n=$1; shift; local D=$T/prof/$n.out
    P40_ZSTD=${P40_ZSTD:-3} P40_L1_RAW=0 perf record -q -F 4000 -e cycles:u -o $T/prof/$n.data "$@" >/dev/null 2>&1
    perf report -i $T/prof/$n.data --stdio --no-children --sort symbol -n --percent-limit 0.2 2>/dev/null | grep -v '^#' | grep -v '^$' > $T/prof/$n.txt; }
  pr bn $N l1 book $BN DOGEUSDT_orderbook.parquet $T/prof/bn.out 1
  pr okx $N l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $T/prof/okx.out 1
  pr bybit $N l1 bybit $Z $T/prof/bybit.out
  pr gate $N l1 gate $S/gate DOGE_USDT 20260917 $T/prof/gate.out 1 1
  rm -rf $T/prof/*.out; wc -l $T/prof/*.txt ;;
ins5) # TK-516 круг 4: cycles:u на одном ядре (taskset $CPU), base/new/pgo попеременно, медиана $REP (по умолчанию 5)
  PG=$T/pgo/puse/release/pq2bin; V="base new"; [ -x $PG ] && V="base new pgo"; [ -n "$NV" ] && V="base new nv"
  rm -rf $T/ins5; mkdir -p $T/ins5; : > $T/ins5/raw.txt
  st() { local n=$1 b=$2; shift 2; local ev=""; [ $b = nv ] && ev=P40_NOVIEW=1
    taskset -c ${CPU:-7} env P40_L1_RAW=0 $ev perf stat -x, -e cycles:u "$@" 2>&1 >/dev/null | awk -F, -v n=$n -v b=$b '/cycles/{printf "%s %s %.3f\n", n, b, $1/1e9}' >> $T/ins5/raw.txt; }
  for r in $(seq 1 ${REP:-5}); do for v in $V; do bin=$B; [ $v = new ] && bin=$N; [ $v = nv ] && bin=$N; [ $v = pgo ] && bin=$PG; D=$T/ins5/$v
    st bn $v $bin l1 book $BN DOGEUSDT_orderbook.parquet $D/bn 1
    st okx $v $bin l1 book $OK DOGE-USDT-SWAP_orderbook.parquet $D/okx 1
    st bybit $v $bin l1 bybit $Z $D/bybit
    st gate $v $bin l1 gate $S/gate DOGE_USDT 20260917 $D/gate 1 1
  done; done
  for n in bn okx bybit gate; do for v in $V; do
    m=$(awk -v n=$n -v b=$v '$1==n&&$2==b{print $3}' $T/ins5/raw.txt | sort -n | awk '{a[NR]=$1} END{printf "%.2f", a[int((NR+1)/2)]}')
    all=$(awk -v n=$n -v b=$v '$1==n&&$2==b{printf "%s ", $3}' $T/ins5/raw.txt)
    echo "$n $v med $m G  [ $all]"
  done; done ;;
pgo) # PGO только pq2bin (В-32): профиль на runs(), гейт pgo = не-pgo (после распаковки .zst), бинарь $T/pgo/puse/release/pq2bin
  export PATH=$PATH:/root/.cargo/bin; W=$T/pgo; LP=/root/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata
  rm -rf $W; mkdir -p $W/pd; cd $Q/pq2bin || exit 1
  RUSTFLAGS="-Cprofile-generate=$W/pd" nice -n 5 cargo build --release --target-dir $W/pgen 2>&1 | tail -1
  runs $W/pgen/release/pq2bin $W/train 2
  $LP merge -o $W/m.profdata $W/pd/*.profraw || exit 1
  RUSTFLAGS="-Cprofile-use=$W/m.profdata" nice -n 5 cargo build --release --target-dir $W/puse 2>&1 | tail -1
  rm -rf $W/o; runs $N $W/o/new 2; runs $W/puse/release/pq2bin $W/o/pgo 2
  n=0; bad=0; for f in $(cd $W/o/new && find . -type f | sort); do n=$((n+1))
    case $f in *.zst) zstd -dc $W/o/new/$f | cmp -s - <(zstd -dc $W/o/pgo/$f) || bad=$((bad+1));; *) cmp -s $W/o/new/$f $W/o/pgo/$f || bad=$((bad+1));; esac; done
  echo "PGO: файлов $n, различий pgo/не-pgo $bad"; [ $bad = 0 ] && [ $n -gt 100 ] || fail=1 ;;
bt) # TK-516 круг 4: P40_BATCH (строк в пачке arrow) для bn/okx, cycles:u на одном ядре, попеременно, медиана ${REP:-3}
  rm -rf $T/bt; mkdir -p $T/bt; : > $T/bt/raw.txt
  for r in $(seq 1 ${REP:-3}); do for bs in ${BS:-4096 16384 65536 262144}; do
    for src in bn okx; do D=$T/bt/$bs/$src; rm -rf $D
      case $src in bn) a="book $BN DOGEUSDT_orderbook.parquet $D 1" ;; okx) a="book $OK DOGE-USDT-SWAP_orderbook.parquet $D 1" ;; esac
      taskset -c ${CPU:-7} env P40_BATCH=$bs P40_L1_RAW=0 perf stat -x, -e cycles:u $N l1 $a 2>&1 >/dev/null | awk -F, -v n=$src -v b=$bs '/cycles/{printf "%s %s %.3f\n", n, b, $1/1e9}' >> $T/bt/raw.txt
  done; done; done
  for src in bn okx; do for bs in ${BS:-4096 16384 65536 262144}; do
    m=$(awk -v n=$src -v b=$bs '$1==n&&$2==b{print $3}' $T/bt/raw.txt | sort -n | awk '{a[NR]=$1} END{printf "%.2f", a[int((NR+1)/2)]}')
    echo "$src batch=$bs med $m G"
  done; done ;;
*) echo "build|test|ab|bench|ins|zl|prof|ins5|pgo|bt"; fail=2 ;;
esac
exit $fail
