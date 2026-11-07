# общие функции конвейера калибровки с Gate (TK-493, задача C): запуск этапов с журналом, остановка на первой ошибке
C2=$HOME/xdl9/c2g
export C2_HOME=$C2
CODE=$C2/repo/tools/compute/statarb/xdl9/conv
PY=$HOME/xdl9/venv/bin/python
TD=$C2/turn
LOG=$C2/logs
TH=${TH:-4}
cd "$CODE" || exit 2
mkdir -p "$LOG"
run() {
  local name=$1; shift
  echo START $(date -u +%FT%TZ) > "$LOG/$name.log"
  nice -n 10 "$@" >> "$LOG/$name.log" 2>&1
  local rc=$?
  echo "EXIT=$rc $(date -u +%FT%TZ)" >> "$LOG/$name.log"
  echo "$(date -u +%FT%TZ) $name rc=$rc" >> "$LOG/pipe.status"
  if [ $rc -ne 0 ]; then echo "FAIL $name" >> "$LOG/pipe.status"; exit 1; fi
}
