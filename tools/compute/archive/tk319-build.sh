#!/usr/bin/env bash
# TK-319 (П-21): сборка корней спота на все монето-сутки (после гейта B зелёного): свечи → σ240 → корни → касания D20 → режим BTC спот.
#   tk319-build.sh klines   свечи spot 1m (188 монет + BTC/ETH, 2026-01-01..30.09, сеть, -P8) и σ240 (sigma-table, все монеты)
#   tk319-build.sh touches  корни эпох (jan..sep) + касания D20 по суткам (дни параллельно NX=15, TP=1 внутри суток) + режим
# Выходы: /data/tk0319/{klines,sigma240,e-<мес>,jrn/<сутки>,regime/<мес>}; маркеры $K/klines.done, $K/touches.done, ошибки — $K/build.fail.
set -uo pipefail
K=/data/tk0319; B=$K/bin; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; NX=${NX:-15}
SINCE=2026-01-01; UNTIL=2026-09-30
: > /dev/null
case "${1:-}" in
klines)
  mkdir -p "$K/klines" "$K/sigma240"; rm -f "$K/klines.done"
  { cat "$K/syms.txt"; echo BTCUSDT; echo ETHUSDT; } | sort -u | grep -v '^$' \
    | xargs -P8 -I{} bash -c 'python3 "$0/ref-klines.py" --category spot --out-dir "$1/klines" --symbols {} --since "$2" --until "$3" > "$1/klines/log-{}.txt" 2>&1 || echo "{} KLINES_FAIL" >> "$1/build.fail"' "$B" "$K" "$SINCE" "$UNTIL"
  # sigma-table отказывает на монете без свечей — считаем по каждой монете, у которой файл свечей есть
  have=$(for s in $(cat "$K/syms.txt"); do [ -s "$K/klines/ref-$s-1m.csv" ] && echo "$s"; done | paste -sd, -)
  python3 "$B/sigma-table.py" --klines "$K/klines" --out-dir "$K/sigma240" --symbols "$have" 2>&1 | tail -3
  echo "klines=$(ls "$K"/klines/ref-*-1m.csv | wc -l) sigma=$(ls "$K"/sigma240/sigma-*.csv | wc -l)" | tee "$K/klines.done"
  ;;
touches)
  for m in jan feb mar apr may jun jul aug sep; do python3 "$B/tk319-spot-roots.py" epoch $m | tail -1; done
  awk -F, 'NR>1{print $1" "$2}' "$K/b1-symdays.csv" | sort -u -k2,2 \
    | xargs -P"$NX" -L1 bash -c 'K='"$K"' BIN='"$BIN"' TP=1 bash '"$B"'/tk319-jrn-day.sh "$0" "$1"'
  cat "$K"/jrn/*/fail.txt > "$K/build.fail.touches" 2>/dev/null
  # режим BTC спот (Q-2): ждём свечи (klines.done), потом по суткам
  while [ ! -e "$K/klines.done" ]; do sleep 60; done
  mkdir -p "$K/jrn-view"
  awk -F, 'NR>1{print $1" "$2}' "$K/b1-symdays.csv" | sort -u -k2,2 | while read -r m d; do
    RG=$K/regime/$m; mkdir -p "$RG"
    ln -sfn "$K/jrn/$d/wt/sub/D20/$d" "$K/jrn-view/$d"
    for s in BTCUSDT ETHUSDT; do ln -sf "$K/klines/ref-$s-1m.csv" "$RG/ref-$s-1m.csv"; done
    python3 "$B/regime.py" --day "$d" --touches "$K/jrn-view" --regime-dir "$RG" 2>&1 | tail -1
  done
  echo "touches_days=$(ls -d "$K"/jrn/*/ | wc -l) fail=$(wc -l < "$K/build.fail.touches")" | tee "$K/touches.done"
  ;;
*) sed -n 2,6p "$0"; exit 2 ;;
esac
