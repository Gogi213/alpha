#!/bin/bash
# Строки `g` (RTT нашего WS-пинга) файла данных: сколько, максимум, сколько дольше 1 с — для сопоставления с всплесками «опозданий» (задержка данных без задержки pong = биржа держит сообщения, а не канал).
# usage: bingx_gping.sh <файл .tsv.zst>
zstdcat $1 2>/dev/null | mawk -F'\t' '{ rx += $2 } $1 == "g" { n++; v = $3 + 0; if (v > m) m = v; if (v > 1000000) { big++; printf "%.0f %d\n", rx / 1e6, v } } END { print "g rows", n, "max us", m, "over 1s", big + 0 }'
