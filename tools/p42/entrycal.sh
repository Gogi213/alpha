#!/usr/bin/env bash
# П-42 §13.16, калибровка W: на srv под systemd-run (nice 19, до 6 процессов) читает закрытые файлы book*.tsv.zst коллектора Binance за
# 12.11 и 13.11 UTC и для каждого сигнала из <сигналы> находит последнюю запись bookTicker ≤ T_ord (entrycal_book.awk).
# Использование на srv: bash entrycal.sh <файл сигналов "символ<TAB>T_ord"> <каталог результата>
set -euo pipefail
SIGS="${1:?файл сигналов}"; OUT="${2:?каталог результата}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"; rm -f "$OUT"/*.txt
ls /data/collect/binance/data/2026-11-12/book*.tsv.zst /data/collect/binance/data/2026-11-13/book*.tsv.zst \
  | while read -r f; do [ -e "$f.ok" ] && echo "$f"; done > "$OUT/files.lst"
echo "файлов: $(wc -l < "$OUT/files.lst")"
export SIGS OUT HERE
xargs -P 6 -I{} bash -c 'f="{}"; n="$(basename "$(dirname "$f")")-$(basename "$f" .tsv.zst)"; zstd -dc "$f" 2>/dev/null | LC_ALL=C mawk -v SIGS="$SIGS" -f "$HERE/entrycal_book.awk" > "$OUT/$n.txt" || true' < "$OUT/files.lst"
touch "$OUT/DONE"
echo "готово: $(cat "$OUT"/*.txt | wc -l) строк"
