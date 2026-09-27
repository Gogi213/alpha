#!/usr/bin/env bash
# Судья: независимый пересчёт денег клетки П-05 из rounds.csv (без portfolio-sim): $ = qty × entry_vwap × net_bps/1e4 (как portfolio-sim),
# без TRX, исполненные (fill_frac > 0), по дню входа. Аргументы: партия набор.
cd ~/alpha
B="$1"; SET="$2"
for ep in "август:epochs/e-aug" "история:tmp-lsk0914.used-20260926/home" "запись:tmp-t29/rec"; do
  name=${ep%%:*}; root=${ep#*:}
  cat "$root"/b5/"$B"/2026-0[89]-*/"$SET"/rounds.csv 2>/dev/null | awk -F, -v name="$name" '
    /^#/ || /^symbol/ { next }
    $1 == "TRXUSDT" { next }
    $9+0 > 0 { e = ($14+0 > 0) ? $14 : $7; u = $9 * e * $10 / 1e4; n++; usd += u; d[$2] += u }
    END { nd=0; for (k in d) nd++; printf "%s: сделок %d, $ %.2f, суток %d\n", name, n, usd, nd }'
done
