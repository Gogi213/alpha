#!/usr/bin/env bash
# TK-393 dl10: хвост Solana (21.10 15–23 ч) второй net-заявкой с конца; основная dl9 идёт с начала и пропустит .done. Только закачка сегментов, разбор делает dl9.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; T0=1792022400; T1=1792713599
cd "$S" || exit 1
SEG_FROM=20261021-15 python3 -u tk387-sqd-sol.py mints-tk393-sol.csv "$R/sol" $T0 $((T1+1)) 24 8 >> "$R/sol-tail.log" 2>&1
echo done > "$R/SOLTAIL.done"
