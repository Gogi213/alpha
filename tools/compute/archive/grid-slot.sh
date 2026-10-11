#!/usr/bin/env bash
# О5 (T-23, Судья b86eed6; CEO 26.09): общие слоты `bounce-grid` на Steam Deck — не больше GRID_SLOTS (2)
# процессов `bounce-grid` разом по всем скриптам (ночь, титрования, П-02, гейты). Каждый процесс — до ~5,7 ГБ
# (до Р6), 14 ГБ RAM: без общего лимита скрипты, каждый со своим DAY_JOBS, уходили в своп до 6,3 ГБ
# (`alpha-p02b-*`, 25.09; docs/findings/backtest-optimization-2026-09-26.md, раздел T-23).
#
#   grid-slot.sh <команда> [аргументы...]
#     ждёт свободный слот, держит его всё время команды, отдаёт код возврата команды. Слот — `flock` на файле
#     $GRID_SLOT_DIR/slot-<i>: снимается сам при смерти процесса (kill -9, OOM), зависших меток нет.
#
# Окружение:
#   GRID_SLOTS=2          число слотов (общее для всех — не менять в одном вызове)
#   GRID_SLOT_N=1         сколько слотов взять: 2 — для тяжёлых суток (LSKUSDT 14.09 ~9–13 ГБ) — один на всю машину
#   GRID_SLOT_DIR         каталог меток, по умолчанию $ALPHA_HOME/study/.grid-slots
#   GRID_SLOT_POLL=5      пауза между попытками, с
#
# Приоритета ночи нет (владелец 27.09, устав `de1a779`: ночь — только наблюдение, уступает счёту; правило
# «к 01:50 UTC свои задания остановить» снято): ночь и дневные вызовы ждут общие слоты одинаково, а сама ночь
# перед стартом ждёт, пока идёт чужой `bounce-grid` (`night-wait.sh`, ExecStartPre юнита).
#
# Порядок захвата при GRID_SLOT_N=2 — строго slot-1, затем slot-2, с ожиданием: однослотовые вызовы берут слот без
# ожидания (flock -n) и не ждут, держа слот, поэтому взаимной блокировки нет.
set -uo pipefail
[ $# -ge 1 ] || { echo "grid-slot: нужна команда" >&2; exit 2; }
SLOTS="${GRID_SLOTS:-2}"
N="${GRID_SLOT_N:-1}"
DIR="${GRID_SLOT_DIR:-${ALPHA_HOME:-$HOME/alpha}/study/.grid-slots}"
POLL="${GRID_SLOT_POLL:-5}"
case "$N" in 1|2) ;; *) echo "grid-slot: GRID_SLOT_N=$N — только 1 или 2" >&2; exit 2;; esac
[ "$N" -le "$SLOTS" ] || { echo "grid-slot: GRID_SLOT_N=$N > GRID_SLOTS=$SLOTS" >&2; exit 2; }
mkdir -p "$DIR"
CMD="$*"

said=""
wait_note() { [ -n "$said" ] || { echo "grid-slot: жду слот ($1): $CMD" >&2; said=1; }; }

if [ "$N" -eq 2 ]; then
  exec 3>"$DIR/slot-1"; flock 3
  exec 4>"$DIR/slot-2"; flock 4
  exec "$@"
fi

while :; do
  for i in $(seq 1 "$SLOTS"); do
    exec 3>"$DIR/slot-$i"
    if flock -n 3; then exec "$@"; fi
    exec 3>&-
  done
  wait_note "занято $SLOTS из $SLOTS"
  sleep "$POLL"
done
