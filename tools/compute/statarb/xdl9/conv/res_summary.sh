#!/bin/bash
# TK-493 (GW 07.11): сводка файла res_sample.sh: начальный и минимальный запас диска (разность - пик занятого диска), минимум MemAvailable, пик суммарного RSS и числа процессов.
# Использование:  res_summary.sh OUT.csv
awk -F, 'NR > 1 { if (NR == 2) d0 = $2; if (dmin == "" || $2 < dmin) dmin = $2; if (mmin == "" || $3 < mmin) mmin = $3; if ($4 + 0 > rmax) rmax = $4 + 0; if ($5 + 0 > nmax) nmax = $5 + 0; n++ }
  END { printf "замеров %d; диск: начало %d МиБ, минимум %d МиБ, пик занятого %.2f ГиБ; MemAvailable минимум %d МиБ; суммарный RSS пик %d МиБ при %d процессах\n", n, d0, dmin, (d0 - dmin) / 1024, mmin, rmax, nmax }' "$1"
