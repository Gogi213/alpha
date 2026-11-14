#!/usr/bin/env bash
# П-42 §13: разбор «плохих строк» aggTrades на srv (запускать под systemd-run: Nice=19, CPUQuota=600%, CPUWeight=1).
# По каждому файлу <монета>-aggTrades-<день>.zip: строк, не с цифры (заголовок/пусто), не 7 полей, цена не «цифры.цифры»
# (в т. ч. запись с E), время не целое. Печать: монета день всего не_цифра не7 цена_не_дес цена_с_E время_не_цел пример.
# Использование: bash scan-bad-rows.sh <каталог aggtrades> <выход .tsv> [потоков] [монеты через запятую; по умолчанию все]
set -euo pipefail
DIR="${1:?каталог}"; OUT="${2:?выход}"; J="${3:-6}"; SYMS="${4:-}"
export DIR
one() {
  f="$1"; b="$(basename "$f" .zip)"; sym="${b%%-aggTrades-*}"; day="${b##*aggTrades-}"
  unzip -p "$f" 2>/dev/null | LC_ALL=C awk -F, -v s="$sym" -v d="$day" '
    { n++ }
    $0 !~ /^[0-9]/ { nd++; next }
    NF != 7 { nf++; if (!ex) ex = $0; next }
    $2 !~ /^[0-9]+(\.[0-9]*)?$/ { npx++; if ($2 ~ /[Ee]/) nE++; if (!ex) ex = $0; next }
    $6 !~ /^[0-9]+$/ { nts++; if (!ex) ex = $0 }
    END { printf "%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%s\n", s, d, n, nd, nf, npx, nE, nts, ex }'
}
export -f one
if [ -n "$SYMS" ]; then ROOTS=(); for s in ${SYMS//,/ }; do ROOTS+=("$DIR/$s"); done; else ROOTS=("$DIR"); fi
find "${ROOTS[@]}" -name '*-aggTrades-*.zip' -print0 | xargs -0 -P "$J" -n 1 bash -c 'one "$0"' > "$OUT"
echo "готово: $(wc -l < "$OUT") файлов"
