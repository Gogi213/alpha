#!/usr/bin/env bash
# TK-486: замер матричного предфильтра формирования (src/statarb/corr.rs) против прямого расчёта пары.
# Запуск на calc через alsched: bash corr-bench.sh <бинарь> ; вывод — строки «naive» и «corr» по потокам.
set -u
BIN="${1:?бинарь alpha}"
M=129600   # 90 суток по 1 м
echo "== 300 символов (≈45 тыс. пар на биржу), потоки 1/8"
for T in 1 8; do "$BIN" statarb corr-bench --symbols 300 --minutes $M --min-obs 1000 --threads $T --check-pairs 200; done
echo "== 900 символов (≈404 тыс. пар, вся вселенная трёх бирж), потоки 1/8"
for T in 1 8; do "$BIN" statarb corr-bench --symbols 900 --minutes $M --min-obs 1000 --threads $T --check-pairs 2000; done
