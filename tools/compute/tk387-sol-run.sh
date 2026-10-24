#!/usr/bin/env bash
# TK-387 (G-016): Solana gTFA 01.09–14.10 от листинга на Gate. usage: tk387-sol-run.sh <out> [cap=350000] — ключ из /root/tk0364/helius.env.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
set -a; . /root/tk0364/helius.env; set +a
cd /data/tk0387 || exit 1
exec python3 -u tk387-sol-gtfa.py pools-tk387-sol.csv "${1:?out}" "${2:-350000}" 8
