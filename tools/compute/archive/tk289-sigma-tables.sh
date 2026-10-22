#!/usr/bin/env bash
# TK-289 П-18 ось 8: σ-таблицы окон 60…960 мин по месяцам (sigma-table.py --window-min) — боковые таблицы для --sigma-from.
#   Выход /data/tk0289/sig/sigma<W>/<мес>/sigma-<SYM>.csv ; те же монеты и те же свечи, что у готовой sigma240 эпохи.
#   Окно 240 считается тоже и сверяется с готовой sigma240 эпохи (diff -rq → /data/tk0289/sig/check240.txt) — гейт «байт в байт».
#   Повтор — дельта: готовые (месяц, окно) (метка sigma<W>/<мес>.ok) пропускаются. Маркер конца: sig/done (rc 0) или sig/fail.
set -uo pipefail
S=/data/tk0289/sig; ST=/data/tk0289/sigma-table.py
mkdir -p "$S"; rm -f "$S/done" "$S/fail"
for m in jan feb mar apr may jun jul aug sep; do
  E=/data/tk046/$m/home/alpha/epochs/e-$m/study/sigma240
  syms=$(ls "$E" | sed 's/^sigma-//;s/\.csv$//' | paste -sd,)
  kl=(--klines "/data/tk046/$m/study/klines"); [ -d "/data/tk200/klines/$m" ] && kl+=(--klines "/data/tk200/klines/$m")
  for W in 240 60 90 120 180 360 480 720 960; do
    out=$S/sigma$W/$m; [ -e "$out.ok" ] && continue
    rm -rf "$out"; mkdir -p "$S/sigma$W"
    if python3 "$ST" "${kl[@]}" --out-dir "$out" --symbols "$syms" --window-min "$W" > "$out.log" 2>&1; then
      touch "$out.ok"
    else
      echo "$m $W: sigma-table rc≠0 (см. $out.log)" >> "$S/fail"
    fi
  done
  if [ -e "$S/sigma240/$m.ok" ]; then
    if diff -rq "$E" "$S/sigma240/$m" > "$S/check240-$m.diff" 2>&1; then echo "$m: 240 байт в байт с готовой ($(ls "$E" | wc -l) файлов)"; else echo "$m: 240 РАСХОДИТСЯ ($(wc -l < "$S/check240-$m.diff") строк diff)"; fi
  fi
done > "$S/check240.txt"
[ -e "$S/fail" ] && exit 3
echo ok > "$S/done"
