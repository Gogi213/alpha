#!/bin/bash
# TK-580 v2 (srv): record-hash comparison of the copies against the LIVE BingX collector (same symbols, window = lifetime of the copies) and OLD vs NEW.
# usage: tk580v2_cmp.sh <label>      output: /data/tk580/v2/<label>/compare.txt ; run as a nice-19 unit (reads ~3 GB of live files)
D=/data/tk580/v2/$1
BIN=/data/tk580/alpha-new2
FROM=$(awk -F'\t' 'NR==1 {printf "%d", $3 * 1e9}' $D/phases.tsv)
TO=$(awk -F'\t' 'END {printf "%d", $4 * 1e9}' $D/phases.tsv)
LIVE=/data/collect/bingx/data
{
  echo "window rx_ns $FROM .. $TO ($(date -u -d @$((FROM / 1000000000)) +%T) .. $(date -u -d @$((TO / 1000000000)) +%T) UTC)"
  for c in NEW OLD; do
    echo "--- LIVE (A, same symbols as B only) vs $c (B)"
    $BIN collect compare --a $LIVE --b $D/$c/data --rx-from-ns $FROM --rx-to-ns $TO --only-b-symbols --bingx-ids
  done
  echo "--- OLD (A) vs NEW (B)"
  $BIN collect compare --a $D/OLD/data --b $D/NEW/data --rx-from-ns $FROM --rx-to-ns $TO --bingx-ids
  for c in NEW OLD; do
    echo "--- $c health at end: gzip / totals"
    python3 - $D/$c/health.json <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
print({k: h.get(k) for k in ("gzip", "totals")})
PY
  done
} > $D/compare.txt 2>&1
echo done > $D/compare.done
