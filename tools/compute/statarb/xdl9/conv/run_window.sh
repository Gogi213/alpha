#!/bin/bash
# TK-493, П-38 r3 (GW 07.11): ЕДИНСТВЕННЫЙ запуск торгового окна C2 на Steam Deck. Токен (go_window.token) записывает ТОЛЬКО CEO (текст - token_body.txt.pre без метки GO).
# Использование (CEO), каталог кода - развёртка `git archive` (tools/compute/statarb/xdl9/conv/):
#   bash run_window.sh check   - проверки без запуска и без захвата токена: токен-заготовка = фактический манифест, token-check, нет чужих счётов, df/free/loadavg
#   bash run_window.sh         - те же проверки, затем запуск окна юнитом `systemd-run --user` (MemoryMax=12G, nice 10, потоки 5); управление возвращается сразу
#   bash run_window.sh inner   - (служебное) тело юнита: df/free до и после, замер ресурсов, раннер, сводка
# Переменные: C2 (~/xdl9/c2), OUT ($C2/window), THREADS (5), MEMMAX (12G), NICE (10), SPEC (<корень кода>/docs/research/P-38-calib-gate-2026-11-07/window_spec_prod.json), PY (~/xdl9/venv/bin/python).
# Выходы раннера - $OUT (на момент запуска пуст либо содержит только token_body.txt.pre[.sha256]); журналы запуска - $C2/window_logs/ (после прогона копируются в $OUT/logs/).
# Коды раннера: 0 - готово; 3 - отказ защиты (токен/хэш/режим; окно не читалось); 75 - инфраструктура до токена (диск < порога, память, потоки; токен НЕ тронут - устранить и повторить);
# 4 - основной набор записан, блок «с Gate» не посчитан (токен сгорел, gate/FAILED.txt); 1 - иное исключение: смотреть последнее событие в $C2/window_reads.log
# (aborted_before_read - токен возвращён, повторить; first_window_read - токен сгорел, повтор только по решению CEO).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../../../../.." && pwd)
C2=${C2:-$HOME/xdl9/c2}
OUT=${OUT:-$C2/window}
THREADS=${THREADS:-5}
MEMMAX=${MEMMAX:-12G}
NICE=${NICE:-10}
PY=${PY:-$HOME/xdl9/venv/bin/python}
SPEC=${SPEC:-$ROOT/docs/research/P-38-calib-gate-2026-11-07/window_spec_prod.json}
LOGD=$C2/window_logs
export C2_HOME=$C2
MODE=${1:-run}

snapshot() {   # df, free, loadavg
  echo "--- $1 $(date -u +%FT%TZ)"
  df -h /home | tail -2
  free -m | head -2
  echo "loadavg: $(cat /proc/loadavg)"
}

checks() {
  local rc=0
  [ -f "$HERE/conv_window.py" ] || { echo "нет conv_window.py рядом со скриптом"; return 2; }
  [ -f "$SPEC" ] || { echo "нет spec: $SPEC"; return 2; }
  if [ -e "$C2/window_reads.log" ]; then
    echo "ВНИМАНИЕ: $C2/window_reads.log уже существует (журнал прежних запусков окна); незавершённая запись блокирует прогон (код 3)"
    tail -3 "$C2/window_reads.log"
  fi
  if pgrep -f 'conv_(run|window)\.py' > /dev/null; then
    echo "ОТКАЗ: на Deck идёт другой счёт C2 (conv_run.py / conv_window.py): окно считается без других тяжёлых задач"
    pgrep -fa 'conv_(run|window)\.py' | head -3
    return 2
  fi
  if systemctl --user is-active --quiet c2-window.service 2> /dev/null; then
    echo "ОТКАЗ: юнит c2-window уже идёт"
    return 2
  fi
  mkdir -p "$OUT"
  if [ -f "$OUT/token_body.txt.pre" ]; then
    $PY "$HERE/conv_window.py" token-body --spec "$SPEC" --label "GO <дата>, CEO" > "$C2/.token_body_now.txt" || { echo "ОТКАЗ: token-body"; return 3; }
    if diff <(head -n -1 "$OUT/token_body.txt.pre") <(head -n -1 "$C2/.token_body_now.txt") > /dev/null; then
      echo "токен-заготовка (token_body.txt.pre) = фактический манифест: OK"
    else
      echo "ОТКАЗ: token_body.txt.pre не равна фактическому манифесту (код/spec/входы изменены после заготовки)"
      rc=3
    fi
    rm -f "$C2/.token_body_now.txt"
  else
    echo "заготовки $OUT/token_body.txt.pre нет (не обязательна)"
  fi
  [ $rc -ne 0 ] && return $rc
  $PY "$HERE/conv_window.py" token-check --spec "$SPEC"
  rc=$?
  snapshot "check"
  return $rc
}

case "$MODE" in
check)
  checks
  exit $?
  ;;
inner)
  mkdir -p "$LOGD"
  snapshot "до запуска" > "$LOGD/pre.txt" 2>&1
  bash "$HERE/res_sample.sh" "$LOGD/resources.csv" 5 'conv_window.py run' &
  SPID=$!
  $PY "$HERE/conv_window.py" run --spec "$SPEC" --go-window --threads "$THREADS" --out "$OUT" > "$LOGD/run.log" 2>&1
  RC=$?
  kill "$SPID" 2> /dev/null
  { snapshot "после запуска"; bash "$HERE/res_summary.sh" "$LOGD/resources.csv"; echo "EXIT=$RC"; } > "$LOGD/post.txt" 2>&1
  echo "$RC" > "$LOGD/exit.txt"
  if [ -d "$OUT" ]; then
    mkdir -p "$OUT/logs"
    cp "$LOGD/pre.txt" "$LOGD/post.txt" "$LOGD/resources.csv" "$LOGD/run.log" "$LOGD/exit.txt" "$OUT/logs/" 2> /dev/null
  fi
  exit $RC
  ;;
run)
  checks || { rc=$?; echo "проверки не пройдены (код $rc): запуск не выполнен, токен не тронут"; exit $rc; }
  mkdir -p "$LOGD"
  rm -f "$LOGD/exit.txt"
  systemd-run --user --unit=c2-window --collect -p MemoryMax="$MEMMAX" --nice="$NICE" --setenv=C2="$C2" --setenv=OUT="$OUT" --setenv=THREADS="$THREADS" --setenv=SPEC="$SPEC" --setenv=PY="$PY"       --setenv=PYTHONDONTWRITEBYTECODE=1 bash "$HERE/run_window.sh" inner
  echo "запущено: юнит c2-window (потоков $THREADS, MemoryMax $MEMMAX, nice $NICE). Следить: tail -f $LOGD/run.log; код выхода: cat $LOGD/exit.txt; журнал защиты: $C2/window_reads.log"
  ;;
*)
  echo "режим: check | run (по умолчанию) | inner"
  exit 2
  ;;
esac
