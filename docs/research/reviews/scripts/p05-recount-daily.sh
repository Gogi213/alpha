#!/usr/bin/env bash
# Судья: по суткам (день входа) — сделок и $ клетки П-05 из rounds.csv, без TRX. Аргументы: партия набор.
cd ~/alpha
B="$1"; SET="$2"
for root in epochs/e-aug tmp-lsk0914.used-20260926/home tmp-t29/rec; do
  cat "$root"/b5/"$B"/2026-0[89]-*/"$SET"/rounds.csv 2>/dev/null
done | awk -F, '/^#/ || /^symbol/ { next } $1 == "TRXUSDT" { next }
  $9+0 > 0 { e = ($14+0 > 0) ? $14 : $7; d[$2] += $9 * e * $10 / 1e4; c[$2]++ }
  END { for (k in d) printf "%s %d %.2f\n", k, c[k], d[k] }' | sort
