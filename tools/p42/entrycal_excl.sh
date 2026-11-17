#!/usr/bin/env bash
# П-42 §13.16, правка 4 (TK-585 v3): разбивка исключённых сигналов калибровки — «монеты нет в пуле» / «в пуле, записи нет в 10 с до T».
# На srv под systemd-run (nice 19). Использование: bash entrycal_excl.sh <файл сигналов "символ<TAB>T_ord"> <каталог результата> <файлы.lst>
set -euo pipefail
SIGS="${1:?файл сигналов}"; OUT="${2:?каталог результата}"; LST="${3:?files.lst}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"; rm -f "$OUT"/*.txt
export SIGS OUT HERE
xargs -P 6 -I{} bash -c 'f="{}"; n="$(basename "$(dirname "$f")")-$(basename "$f" .tsv.zst)"; zstd -dc "$f" 2>/dev/null | LC_ALL=C mawk -v SIGS="$SIGS" -f "$HERE/entrycal_excl.awk" > "$OUT/$n.txt" || true' < "$LST"
touch "$OUT/DONE"
echo "готово: $(cat "$OUT"/*.txt | wc -l) строк"
