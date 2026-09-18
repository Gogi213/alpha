#!/usr/bin/env bash
# Касания всех сверенных монет записи на счётной машине (исследование порога
# В-61): `lob touches --h3-mode floor` (k = 1.0 из instruments.csv — касания
# ЛЮБОГО уровня, чтобы распределение было полным) по каждому символу с
# маркером verify-<SYM>.status == ok, по три параллельно, под nice.
#   sudo tools/compute/touches-all.sh [root] [out-dir] [jobs]
set -euo pipefail
ROOT="${1:-/opt/alpha-compute/root}"
OUT="${2:-/opt/alpha-compute/study/touches}"
JOBS="${3:-3}"
BIN=/opt/alpha-compute/bin/alpha
mkdir -p "$OUT"
ls "$ROOT"/verify-*.status | while read -r f; do
  s=$(basename "$f" .status); s=${s#verify-}
  [ "$(cat "$f")" = "ok" ] && echo "$s"
done > "$OUT/symbols.txt"
echo "symbols: $(wc -l < "$OUT/symbols.txt")"
xargs -P "$JOBS" -I{} -a "$OUT/symbols.txt" nice -n 15 bash -c \
  "$BIN lob touches --root '$ROOT' --symbol {} --h3-mode floor --out '$OUT/touches-{}.csv' >'$OUT/{}.log' 2>&1 || echo 'FAILED {}' >> '$OUT/failed.txt'"
echo "done: $(ls "$OUT"/touches-*.csv | wc -l) files, failed: $( [ -f "$OUT/failed.txt" ] && wc -l < "$OUT/failed.txt" || echo 0 )"
