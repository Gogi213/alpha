#!/bin/bash
# «Опоздания» BingX, вторая проходка (collector-perf, 14.11): для каждой записи с лагом > 2 с — что было у того же символа до неё и после: сдвиг E к предыдущему E, промежуток прихода, лаг соседей, совпадение цен.
# Признак «метка идёт назад» (E меньше предыдущего E символа) отличает запоздавшее сообщение (вышло из очереди биржи через 10 с) от «тихого символа» (E старый, потому что изменений не было).
# usage: bingx_late2.sh <файл .tsv.zst> <вывод>; книга (`b`) и mark (`m`) по одному файлу; под systemd-run.
F=$1; O=$2
zstdcat $F 2>/dev/null | mawk -F'\t' '
{ rx += $2 }
$1 == "b" || $1 == "m" {
  k = $1; sym = (k == "m") ? $4 : $5; lag = $3 + 0; ms = rx / 1e6; E = ms - lag
  if (k == "b") { px = $7 "|" $9; du = $6 } else { px = $5; du = "" }
  key = k sym
  if (key in pE) {
    d = E - pE[key]; gap = ms - pMs[key]
    tot[k]++
    if (d < -50) { back[k]++; if (lag > 2000) backLate[k]++ }
    if (lag > 2000) {
      late[k]++
      printf "X\t%s\t%s\t%.0f\t%d\tdE=%d\tgap=%d\tprevlag=%d\tdu=%s\tsameprice=%d\n", k, sym, ms, lag, d, gap, pLag[key], du, (px == pPx[key])
      pend[key] = 1; stalePx[key] = px; staleE[key] = E
    } else if (pend[key]) {
      printf "Y\t%s\t%s\t%.0f\t%d\tdE_vs_stale=%d\tgap=%d\tsame_as_stale=%d\n", k, sym, ms, lag, E - staleE[key], gap, (px == stalePx[key])
      delete pend[key]
    }
  }
  pE[key] = (E > pE[key] || lag <= 2000) ? E : pE[key]   # опорный E — последний «нормальный»: поздняя запись не сдвигает опору
  pMs[key] = ms; pLag[key] = lag; pPx[key] = px
}
END { for (k in tot) printf "T\t%s\t%d\t%d\t%d\t%d\n", k, tot[k], back[k] + 0, late[k] + 0, backLate[k] + 0 }' > $O
