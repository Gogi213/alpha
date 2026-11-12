#!/usr/bin/env bash
# TK-366: список "SYM DAY" → tk366-one.sh параллельно. tk366-run.sh fut|spot <список> ; env: JOBS(умолч. 8) BIN ROOT INSTR LOG TMP
mk=${1:?fut|spot}; LIST=${2:?}; JOBS=${JOBS:-8}; export BIN ROOT INSTR LOG TMP
here=$(cd "$(dirname "$0")" && pwd)
xargs -a "$LIST" -r -P "$JOBS" -n 2 bash -c 'bash "$0/tk366-one.sh" "$1" "$2" "$3"' "$here" "$mk" 2>/dev/null || true
