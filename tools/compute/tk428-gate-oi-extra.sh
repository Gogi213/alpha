#!/bin/bash
# TK-428 (возврат Судьи 2, п.1): Gate contract_stats (OI, ликвидации) + funding_rate 01.05–31.07 по делистнутым фьючерсам вне gate-list
# (139 из gate-extra-list.tsv + U_USDT — для записи ответа API). Тот же скрипт, что п.1 (tk415-oi-funding.py); ответ API при отказе —
# в 7-м столбце $ROOT/gate-oi/cov/gate-{oi,funding}-<SYM>.tsv (колонка note: «http … тело»).
# 28.10 00:42 UTC окно «180 дней» уже не включает 01.05 00:00 — ответ INVALID_PARAM_VALUE «exceeds 180-day limit» даже для живых: K_START=1777680000000 (02.05), ROOT=другой.
# Использование: alsched submit --kind net --name tk428-oi-extra -- env ROOT=/srv/alpha-sda/tk428z K_START=1777680000000 bash /data/tk0428/src/tk428-gate-oi-extra.sh
set -u
root=${ROOT:-/srv/alpha-sda/tk428x}; src=${SRC:-/data/tk0428/src}; d=${D:-/data/tk0428}
mkdir -p "$root/gate-oi" "$root/log"
printf 'биржа,рынок,инструмент,минут_с_данными\n' > "$root/gate-oi/list.csv"
{ awk -F'\t' '$1=="fut"{print $2}' "$d/gate-extra-list.tsv"; echo U_USDT; } | sort -u | awk '{print "gate,futures_usdt," $1 ",1"}' >> "$root/gate-oi/list.csv"
K_START=${K_START:-1777593600000} K_END=1785542400000 python3 -I "$src/tk415-oi-funding.py" gate "$root/gate-oi" "$root/gate-oi/list.csv" 15 >> "$root/log/gate-oi-extra.log" 2>&1
echo "gate-oi-extra rc=$? n=$(($(wc -l < "$root/gate-oi/list.csv")-1)) $(date +%T)" >> "$root/log/run.log"
