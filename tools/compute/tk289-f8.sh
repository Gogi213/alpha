#!/usr/bin/env bash
# TK-289 П-18 F1 ось 8: окно σ-таблицы (60 90 120 180 360 480 720 960 мин; центр 240 — B1 в Летописи) — клетка B1, по проходу на окно (--sigma-from study/sigma<W>), 251 сутки, боевой PGO (В-21).
#   σ-таблицы /data/tk0289/sig2/sigma<W>/<мес>; сперва сверка sig2/sigma240 с эпохой (tk289-check240-vals.py, значения на общих минутах).
#   Порядок суток-первыми: 8 окон одних суток идут рядом (общий кэш чтения). Выход /data/tk0289/f8/<W>/out/run-<мес>-<сутки>/ ; env NX (=ядра, умолч. 15), GT=1. Повтор — дельта.
set -uo pipefail
export REASON="TK-289 П-18 F1 ось 8 σ-окна, B1 x 8 окон, pgo" BIN=/opt/alpha-compute/bin/alpha-tk289-pgo SCRIPTS=/data/tk0289/scripts NX=${NX:-15} GT=${GT:-1} PLAN=L TP=1
R=/data/tk0289/f8; export R; mkdir -p "$R"
python3 /data/tk0289/check240-vals.py > "$R/check240-vals.txt" || { echo "σ240 sig2 расходится с эпохой: $R/check240-vals.txt"; exit 5; }
WS="60 90 120 180 360 480 720 960"
for w in $WS; do
  P=$R/$w/plan; rm -rf "$P"; mkdir -p "$P" "$R/$w/out"
  python3 /data/tk0289/p18_plan.py "$P" --axes 8 --only "$w" --label L > /dev/null || exit 6
  : > "$P/L.cells"; : > "$P/L.args"; echo 2026-01 > "$P/months.txt"
  for d in "$R/$w"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
done
while read -r m d; do for w in $WS; do echo "$w $m $d"; done; done < /data/tk0341/keep/days9.txt > "$R/units.txt"
export BIN SCRIPTS GT REASON PLAN TP
xargs -P "$NX" -L1 bash -c 'w=$1; SIG=sigma$w SIGROOT=/data/tk0289/sig2/sigma$w P=$R/$w/plan O=$R/$w/out bash "$SCRIPTS/day-cells.sh" "run-$2-$3" "$2" "$3"' _ < "$R/units.txt"
rc=$?
n=$(cat "$R"/*/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
