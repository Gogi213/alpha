#!/usr/bin/env bash
# TK-373: Binance USDM aggTrades (data.binance.vision) по символам вселенной, дни 16–21.10. usage: tk373-binance-dl.sh <outdir> <syms.txt>
OUT=$1; SYMS=$2; mkdir -p "$OUT"
dl() { s=$1; d=$2; f="$OUT/$s/$s-aggTrades-$d.zip"; [ -f "$f.done" ] && return; mkdir -p "$OUT/$s"
  code=$(curl -s -m 300 -o "$f" -w '%{http_code}' "https://data.binance.vision/data/futures/um/daily/aggTrades/$s/$s-aggTrades-$d.zip")
  if [ "$code" = 200 ]; then touch "$f.done"; else rm -f "$f"; echo "$s $d $code" >> "$OUT/missing.txt"; fi; }
export -f dl; export OUT
for d in 2026-10-16 2026-10-17 2026-10-18 2026-10-19 2026-10-20 2026-10-21; do while read -r s; do echo "$s $d"; done < "$SYMS"; done \
 | xargs -P 6 -L1 bash -c 'dl $0 $1'
echo "files $(find "$OUT" -name '*.zip' | wc -l) missing $(wc -l < "$OUT/missing.txt" 2>/dev/null)" > "$OUT/summary.txt"; touch "$OUT/binance.done"
