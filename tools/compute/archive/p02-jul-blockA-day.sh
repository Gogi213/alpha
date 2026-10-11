#!/usr/bin/env bash
# TK-018 (В-151): блок A П-02 на ИЮЛЕ 2026 — описание, не проверка. ОДНО задание на сутки (В-136): последовательно
# все проходы, которыми Г-07/Г-46/Г-33 считались на авг/сен, тем же бинарником и теми же параметрами:
#   touches — `p02-stage2-recompute.sh` (Г-07: depth_behind_lots; REPEAT_MS=300000, как `tmp-p02c-recompute.sh`)
#   flow    — `p02-wave3-flow-recompute.sh` + `p02-wave3-flow-join.py` (Г-46: окна 60/300 с, без сдвига — `run.sh flow`)
#   g33     — `p02-g33-recompute.sh` + `p02-g33-causal-join.py` (Г-33 причинная §12; G33_MAX_TRADED=0 — `run-g33.sh`)
# Г-36 отдельного прохода не требует (читает кэш D20 июля в сборщике). Метка суток — <OUT_ROOT>/<день>.done,
# ставится только если у всех трёх проходов есть <день>.done.
#
#   bash ~/alpha/tmp-p02jul/p02-jul-blockA-day.sh 2026-07-DD
set -euo pipefail
day="${1:?сутки YYYY-MM-DD}"
H="$HOME/alpha"
T="${P02JUL_TOOLS:-$H/tmp-p02jul}"
E="$H/epochs/e-jul"
OUT_ROOT="$E/study/p02jul"
mkdir -p "$OUT_ROOT"
if [ -f "$OUT_ROOT/$day.done" ]; then echo "$day: уже готово"; exit 0; fi
export ALPHA_HOME="$E" D20=study/approaches/D20 SRC_PREFIX=study/root- BIN="$H/bin/alpha-3a9fe23" JOBS=2
t0=$(date +%s)

OUT=study/p02jul/touches REPEAT_MS=300000 bash "$T/p02-stage2-recompute.sh" "$day"
t1=$(date +%s)
OUT=study/p02jul/flow JOIN="$T/p02-wave3-flow-join.py" bash "$T/p02-wave3-flow-recompute.sh" "$day"
t2=$(date +%s)
OUT=study/p02jul/g33 JOIN="$T/p02-g33-causal-join.py" G36_RATIO=0 G33_MAX_TRADED=0 bash "$T/p02-g33-recompute.sh" "$day"
t3=$(date +%s)

for p in touches flow g33; do
  [ -f "$OUT_ROOT/$p/$day.done" ] || { echo "$day: проход $p без .done — сутки не закрыты" >&2; exit 1; }
done
echo "$day: touches $((t1-t0)) с, flow $((t2-t1)) с, g33 $((t3-t2)) с, всего $((t3-t0)) с"
touch "$OUT_ROOT/$day.done"
