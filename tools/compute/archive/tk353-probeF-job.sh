#!/usr/bin/env bash
# TK-353: проба C3 «10 монет × 02.2026» (решение Судьи tk353-c3-probe10, 22.10): монеты пула с тиком ≤ 1 бп по обороту, A_B дословно.
# Монеты: тик из заголовков бинлогов фев (мин. mid дня — kline low), оборот — сумма дневных turnover kline фев; verdict «пускаем» 28/28 суток.
set -uo pipefail
D=""; for i in $(seq -w 1 28); do D="$D 2026-02-$i"; done
SYMS="XRPUSDT HYPEUSDT XAUTUSDT FARTCOINUSDT ZECUSDT ENSOUSDT AXSUSDT DOTUSDT TAOUSDT ZROUSDT" DAYS="${D# }" \
REUSE=/data/tk0353/probe10/cache W=/data/tk0353/probeF BIN=/data/tk0353/head/alpha-new NX=15 bash /data/tk0353/tk353-probe10.sh
