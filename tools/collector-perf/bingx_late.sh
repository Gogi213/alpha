#!/bin/bash
# «Опоздания» BingX (collector-perf, 14.11): из файлов данных дня вытащить записи с лагом rx−E > 2000 мс (порог health `late_ms`), счётчики по символам и по секундам, гистограмму лага и служебные строки `c`.
# usage: bingx_late.sh <каталог дня> <каталог вывода> [параллельность=3]; запускать под systemd-run (Nice, CPUQuota). Выход по файлу: <вывод>/<файл>.out строками L/N/S/H/C (см. awk ниже); разбор — bingx_late.py.
DAY=$1; OUT=$2; PAR=${3:-3}
mkdir -p $OUT
one() {
  f=$1; OUT=$2
  zstdcat $f 2>/dev/null | mawk -F'\t' '
  BEGIN { split("100 200 500 1000 2000 3000 5000 10000", B, " ") }
  { rx += $2 }
  $1 == "c" { printf "C\t%.0f\t%s\t%s\n", rx / 1e6, $3, $4; next }
  $1 == "b" || $1 == "t" || $1 == "m" {
    k = $1; lag = $3 + 0; sym = (k == "m") ? $4 : $5
    key = k "\t" sym; n[key]++
    ms = int(rx / 1e6); sec = int(ms / 1000); sk = k "\t" sec
    sn[sk]++; if (lag > sm[sk]) sm[sk] = lag
    b = 9; for (i = 1; i <= 8; i++) if (lag < B[i]) { b = i; break }
    h[k "\t" b]++
    if (lag > 2000) { late[key]++; sl[sk]++; printf "L\t%s\t%s\t%.0f\t%d\t%s\n", k, sym, ms, lag, (k == "b") ? $4 : "" }
  }
  END {
    for (key in n) printf "N\t%s\t%d\t%d\n", key, n[key], late[key] + 0
    for (sk in sl) printf "S\t%s\t%d\t%d\t%d\n", sk, sn[sk], sl[sk], sm[sk]
    for (hk in h) printf "H\t%s\t%d\n", hk, h[hk]
  }' > $OUT/$(basename $f).out
}
export -f one
ls $DAY/*.tsv.zst | xargs -P $PAR -I{} bash -c 'one {} '$OUT
