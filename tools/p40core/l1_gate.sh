#!/bin/bash
# TK-515: G-L1a — ряд L1 (t, bid, bq, ask, aq) из полной книги (.bin) = ряд из L1-файла (.l1.bin), sha256 равны; + МБ сырья/с на ядро (1 поток).
# Источники: Binance и OKX (CHFT parquet, DOGE 17.09), Bybit (zip суток), Gate (24 csv.gz суток). Запуск на srv 5.231.25.224: bash /root/p40core-tk515/l1_gate.sh
# Код выхода ≠ 0 — расхождение. Каталоги: Q — сборка (pq2bin, p40core), S — сырьё Bybit/Gate, T — рабочий.
Q=${Q:-/root/p40core-tk515}; P=$Q/pq2bin/target/release/pq2bin; C=$Q/rs/target/release/p40core
S=${S:-/root/tk515/src}; T=${T:-/tmp/l1_gate}; BN=${BN:-/data/stakan/chft/binance_futures/2026-09-17}; OK=${OK:-/root/p40ref/raw/okx}
rm -rf "$T"; mkdir -p "$T"
fail=0
now() { date +%s.%N; }
dt() { awk -v a="$2" -v b="$1" 'BEGIN{print a-b}'; }
# chk <имя> <байт сырья> <секунд L1-конвертера на 1 потоке>
chk() {
  local n=$1 raw=$2 sec=$3
  $C l1series "$T/$n/full" > "$T/$n.full.txt"; $C l1series "$T/$n/l1" > "$T/$n.l1.txt"
  local a b rows
  a=$(sha256sum < "$T/$n.full.txt" | cut -c1-16); b=$(sha256sum < "$T/$n.l1.txt" | cut -c1-16); rows=$(wc -l < "$T/$n.full.txt")
  local fb lb zb; fb=$(cat "$T/$n"/full/*.bin | wc -c); lb=$(cat "$T/$n"/l1/*.l1.bin | wc -c); zb=$(cat "$T/$n"/l1/*.l1.bin.zst | wc -c)
  local st=OK; [ "$a" = "$b" ] && [ "$rows" -gt 0 ] || { st=РАСХОЖДЕНИЕ; fail=1; }
  awk -v n="$n" -v st="$st" -v a="$a" -v b="$b" -v rows="$rows" -v raw="$raw" -v sec="$sec" -v fb="$fb" -v lb="$lb" -v zb="$zb" \
    'BEGIN{printf "%s G-L1a %s: строк ряда %d sha полная %s l1 %s | сырьё %.1f МБ, L1-конвертер %.1f с = %.1f МБ/с на ядро | полная .bin %.1f МБ, l1.bin %.2f МБ (%.1f%%), zst %.2f МБ\n", n, st, rows, a, b, raw/1e6, sec, raw/1e6/sec, fb/1e6, lb/1e6, 100*lb/fb, zb/1e6}'
}
# CHFT parquet (Binance, OKX)
for src in bn okx; do
  if [ $src = bn ]; then root=$BN; f=DOGEUSDT_orderbook.parquet; else root=$OK; f=DOGE-USDT-SWAP_orderbook.parquet; fi
  raw=$(cat $root/*/$f | wc -c)
  $P book "$root" $f "$T/$src/full" 4 2>/dev/null
  t0=$(now); P40_L1_RAW=1 $P l1 book "$root" $f "$T/$src/l1" 1 2>/dev/null; t1=$(now)
  chk $src $raw "$(dt $t0 $t1)"
done
# Bybit zip суток
z=$S/bybit/DOGEUSDT_2026-09-17_ob200.data.zip
$P bybit "$z" "$T/bybit/full" 2>/dev/null
t0=$(now); P40_L1_RAW=1 $P l1 bybit "$z" "$T/bybit/l1" 2>/dev/null; t1=$(now)
chk bybit "$(stat -c %s "$z")" "$(dt $t0 $t1)"
# Gate: 24 часовых csv.gz
raw=$(cat $S/gate/DOGE_USDT-20260917*.csv.gz | wc -c)
$P gate "$S/gate" DOGE_USDT 20260917 "$T/gate/full" 1 4 2>/dev/null
t0=$(now); P40_L1_RAW=1 $P l1 gate "$S/gate" DOGE_USDT 20260917 "$T/gate/l1" 1 1 2>/dev/null; t1=$(now)
chk gate $raw "$(dt $t0 $t1)"
[ $fail = 0 ] && echo "G-L1a: все 4 ряда совпали" || echo "G-L1a: РАСХОЖДЕНИЕ"
exit $fail
