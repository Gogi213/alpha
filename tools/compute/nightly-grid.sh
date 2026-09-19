#!/usr/bin/env bash
# Ночная сетка на счётной машине (владелец 2026-09-19: «со временем появится ещё день —
# как появится, так потестишь»): после ночного переноса суток (alpha-sync 00:45 UTC на
# коллекторе) гоняет базу В-65 на полах владельца (В-66/67, пол возраста В-70 = 15 мин),
# E7 (В-69), затем вердикты — по ВСЕМ суткам корня (без --day). Испытания каждого вида сетки
# регистрируются в журнале один раз (первая ночь, маркер study/.trials-logged-<вид>).
#
#   /opt/alpha-compute/bin/nightly-grid.sh            # из alpha-grid-nightly.timer (02:00 UTC)
# Артефакты: study/touches/<сутки>/ + study/floors-<сутки>.txt (H3: касания и матрица флоров по
# каждой сутке отдельно — до сеток, чтобы утром матрица была даже если сетки не дойдут),
# b5/nightly-<день>-base/<набор>/ (база и сторона одним процессом, --set) и b5/nightly-<день>-e7-a15-s10-any/,
# вердикты study/bounce-verdict-nightly-<день>-<набор|метка>.csv,
# лог study/nightly-<день>.log. Если сетка предыдущей ночи ещё идёт — выход без запуска.
#
# H3b (19.09, ревью §0): сутки с ошибкой пересчитываются не бесконечно — причина пишется в лог,
# после второй ночи сутки закрываются `.done` (список ошибок — failed-history.txt); архивные сутки
# `*.binlog.zst` читаются тем же `Reader` (проверено: ACE 09-16 → `days=1 touches=1`); шапка
# study/floors-<сутки>.txt несёт оговорку, что трекер суточный (возраст плотности обнуляется в 00:00);
# окно суток для сеток — DAYS_WINDOW (по умолчанию все сутки корня), касания окна не касается.
set -uo pipefail
cd /opt/alpha-compute || exit 1
export PATH=/root/.cargo/bin:$PATH
DAY=$(date -u +%F)
LOG=study/nightly-$DAY.log
# Громкие ошибки (владелец 20.09: «почему если что-то падает — это молчаливо?»): каждая проблема —
# строка в study/ALERTS.log (дата, ночь, что именно), в конце ночи одна строка «ОК/ПРОВАЛ», и при
# любой проблеме скрипт выходит с кодом 1 — юнит alpha-grid-nightly виден в `systemctl --failed`.
# Внешний канал — ALERT_CMD (окружение юнита): команда получает текст первым аргументом, например
# curl к api.telegram.org с токеном из /etc/alpha/alert.env (файл root:600, кладёт владелец).
ALERTS=study/ALERTS.log
NALERTS=0
alert() {
  NALERTS=$((NALERTS + 1))
  echo "$(date -u +%FT%TZ) nightly-$DAY: $*" >> "$ALERTS"
  echo "!! $*" >> "$LOG"
}
finish() {
  if [ "$NALERTS" -eq 0 ]; then
    echo "$(date -u +%FT%TZ) nightly-$DAY: ОК" >> "$ALERTS"
    echo "== $(date -u +%FT%TZ) nightly done: ОК" >> "$LOG"
    [ -n "${ALERT_CMD:-}" ] && $ALERT_CMD "alpha nightly $DAY: ОК" >/dev/null 2>&1
    exit 0
  fi
  echo "$(date -u +%FT%TZ) nightly-$DAY: ПРОВАЛ — проблем $NALERTS (выше)" >> "$ALERTS"
  echo "== $(date -u +%FT%TZ) nightly done: ПРОВАЛ — проблем $NALERTS, см. $ALERTS" >> "$LOG"
  [ -n "${ALERT_CMD:-}" ] && $ALERT_CMD "alpha nightly $DAY: ПРОВАЛ — проблем $NALERTS: $(grep "nightly-$DAY:" "$ALERTS" | tail -n +1 | cut -d: -f4- | tr '\n' ';' | cut -c1-500)" >/dev/null 2>&1
  exit 1
}
BIN=/opt/alpha-compute/bin/alpha
RUNS=study/runs-2026-09-19.csv
# Окно суток для сеток: пусто — все сутки корня (как было); DAYS_WINDOW=4 — последние четыре.
# Нужно потому, что сетки идут по всем суткам и ночь растёт вместе с историей (H3b/§4.3).
DAYS_WINDOW="${DAYS_WINDOW:-}"
# Только касания, без сеток (`TOUCHES_ONLY=1`): H2 готовит study/touches/<сутки>/ заранее, не дожидаясь
# ночи. Именно переменной окружения, а не аргументом, — юнит таймера её не задаёт, и ночные сетки
# пропустить случайно нельзя.
TOUCHES_ONLY="${TOUCHES_ONLY:-}"
DAYS_ALL=$(ls root/*.binlog* 2>/dev/null | sed -E 's/.*-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/' | sort -u)
DAY_ARGS=""
if [ -n "$DAYS_WINDOW" ]; then
  for d in $(echo "$DAYS_ALL" | tail -n "$DAYS_WINDOW"); do DAY_ARGS="$DAY_ARGS --day $d"; done
fi
if systemctl list-units "alpha-grid-*" --no-legend | grep -q running; then
  echo "== $(date -u +%FT%TZ) сетка ещё идёт — ночной прогон пропущен" >> "$LOG"
  alert "ночь пропущена: сетка ещё идёт ($(systemctl list-units "alpha-grid-*" --no-legend | grep running | awk '{print $1}' | tr '\n' ' '))"
  finish
fi
# В-71 (владелец 19.09, вечер): 15 минут с постановки — жёсткий флор (моложе не торгуем);
# возраст выше флора — ось (пул / пулы / по монете — H2 в handoff-2026-09-19.md); сила ×поток ≥ 100 %
# под флором возраста пуста (floors-balance-2026-09-19.md) — сила 10 % и «любая». Контроль —
# прежняя база при силе 100 % без возраста (непрерывность с v66flow/v68lat).
# Касания сеток — из кэша H3 (`study/touches/<сутки>/`, считается выше до сеток; 20.09): реплей книги
# был 83 % времени сетки, гейт «те же rounds/forms» пройден на пяти монетах (COMMANDS.md); монета без
# суток в кэше идёт реплеем сама (строка в grid.err). σ-форм в ночном наборе нет.
USD="--h3-mode notional --h3-usd 10000"
# Только сеткам (у `lob touches` такого флага нет — 20.09 он по ошибке стоял в USD и ронял касания):
GRID="--touches-from study/touches --regime-from study/regime"
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
E7="--stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form half1to1 --take-form eat50x80 --order-qty-mult 2"
# Испытания регистрируются в журнале один раз на вид сетки (первая ночь) — дальше формы те же.
verdict_one() {
  # $1 — вид (имя набора/сетки), $2 — каталог сетки; вердикт study/bounce-verdict-nightly-<день>-<вид>.csv
  local kind=$1 gdir=$2
  local label="nightly-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind" ]; then logflag="--log-trials"; fi
  if $BIN lob bounce-verdict --grid-dir "$gdir" --runs-csv "$RUNS" --out "study/bounce-verdict-$label.csv" $logflag > "study/bounce-verdict-$label.log" 2>&1; then
    [ -n "$logflag" ] && touch "study/.trials-logged-$kind"
  else
    alert "вердикт $label не посчитался: $(tail -2 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-200)"
  fi
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
}
# Все наборы базы одним процессом (`--set`, 20.09): события суток и окна декодируются один раз на
# монету, а не по разу на семью — девять сеток стоят как одна; артефакты b5/nightly-<день>-base/<набор>/.
run_sets() {
  local label="nightly-$DAY-base"
  local setargs=""
  for kv in "$@"; do setargs="$setargs --set $kv"; done
  echo "== $(date -u +%FT%TZ) grid $label start: наборы $*" >> "$LOG"
  THREADS=3 /opt/alpha-compute/bin/run-grid.sh "$label" $USD $GRID $BASE $DAY_ARGS $setargs >> "$LOG" 2>&1
  sleep 5
  while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  grid_check "$label"
  for kv in "$@"; do verdict_one "${kv%%:*}" "b5/$label/${kv%%:*}"; done
}
run_one() {
  local kind=$1; shift
  local label="nightly-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind" ]; then logflag="--log-trials"; fi
  echo "== $(date -u +%FT%TZ) grid $label start" >> "$LOG"
  THREADS=3 /opt/alpha-compute/bin/run-grid.sh "$label" "$@" >> "$LOG" 2>&1
  sleep 5
  while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  grid_check "$label"
  verdict_one "$kind" "b5/$label"
}
# Сетка обязана кончиться штатно: юнит не failed, в grid.err строки «готов», нет «error»/panic.
grid_check() {
  local label=$1
  if systemctl is-failed --quiet "alpha-grid-$label"; then
    alert "сетка $label: юнит failed — $(journalctl -u "alpha-grid-$label" --no-pager -n 3 2>/dev/null | tail -1 | cut -c1-200)"
    systemctl reset-failed "alpha-grid-$label" 2>/dev/null
  fi
  if ! grep -q "готов" "b5/$label/grid.err" 2>/dev/null; then
    alert "сетка $label: ни одной готовой монеты в grid.err"
  fi
  if grep -qiE "^error|panicked|Error:" "b5/$label/grid.err" 2>/dev/null; then
    alert "сетка $label: ошибки в grid.err — $(grep -iE "^error|panicked|Error:" "b5/$label/grid.err" | head -1 | cut -c1-200)"
  fi
}

# H3 (2026-09-19): касания и матрица флоров — по каждой сутке отдельно, до сеток. У `lob touches`
# нет `--day`, а сутки в корне копятся, поэтому на каждую сутку собирается корень-день из симлинков
# (файлы ровно этих суток + маркеры + instruments.csv): резолвер частей читает каталог по именам,
# так что симлинка достаточно. Стоимость ночи не растёт с историей, а артефакт остаётся по суткам —
# его требует H2 (шаг 2: лучший порог на сутках A, ход на сутках B). Сутки, сложенные один раз,
# помечаются `.done` и не пересчитываются; слить сутки для многодневной матрицы — `cat` по символу.
day_root() {
  local day=$1
  local dir="study/root-$day"
  rm -rf "$dir"; mkdir -p "$dir"
  local f
  for f in "root/"*"-$day.binlog" "root/"*"-$day-"*".binlog" \
           "root/"*"-$day.binlog.zst" "root/"*"-$day-"*".binlog.zst" \
           "root/verify-"*".status" root/instruments.csv root/session.json; do
    [ -e "$f" ] || continue
    ln -s "$PWD/$f" "$dir/$(basename "$f")"
  done
}

touches_for_day() {
  local day=$1
  local out="study/touches/$day"
  [ -f "$out/.done" ] && return 0
  mkdir -p "$out"
  local attempt; attempt=$(( $(cat "$out/.attempts" 2>/dev/null || echo 0) + 1 )); echo "$attempt" > "$out/.attempts"
  day_root "$day"
  ls "study/root-$day"/verify-*.status | while read -r f; do
    [ "$(cat "$f")" = "ok" ] || continue
    local s; s=$(basename "$f" .status); echo "${s#verify-}"
  done > "$out/symbols.txt"
  local n; n=$(wc -l < "$out/symbols.txt")
  echo "== $(date -u +%FT%TZ) touches $day start (попытка $attempt): монет с маркером ok $n" >> "$LOG"
  rm -f "$out/failed.txt"
  xargs -r -P 3 -I{} -a "$out/symbols.txt" nice -n 15 bash -c \
    "$BIN lob touches --root 'study/root-$day' --symbol {} $USD --out '$out/touches-{}.csv' >'$out/{}.log' 2>&1 || echo {} >> '$out/failed.txt'"
  local files failed
  files=$(ls "$out"/touches-*.csv 2>/dev/null | wc -l)
  failed=$([ -f "$out/failed.txt" ] && wc -l < "$out/failed.txt" || echo 0)
  echo "== $(date -u +%FT%TZ) touches $day done: файлов $files, ошибок $failed" >> "$LOG"
  [ "$failed" -gt 0 ] && alert "касания $day: ошибок $failed из $n монет ($(head -3 "$out/failed.txt" | tr '\n' ' '))"
  if [ "$failed" -gt 0 ]; then
    { echo "-- попытка $attempt $(date -u +%FT%TZ)"; cat "$out/failed.txt"; } >> "$out/failed-history.txt"
    while read -r sym; do
      echo "   $sym: $(tail -2 "$out/$sym.log" 2>/dev/null | tr '\n' ' ' | cut -c1-200)" >> "$LOG"
    done < "$out/failed.txt"
  fi
  if [ "$files" -ge "$n" ] && [ "$failed" -eq 0 ]; then
    touch "$out/.done"
  elif [ "$failed" -gt 0 ] && [ "$attempt" -ge 2 ]; then
    touch "$out/.done"
    echo "== $(date -u +%FT%TZ) touches $day: две ночи с ошибками — сутки закрыты .done, пересчёт остановлен (failed-history.txt)" >> "$LOG"
  fi
  {
    echo "# трекер: notional \$10k (--h3-usd 10000), сутки одни ($day)"
    echo "# возраст плотности обнуляется в 00:00: трекер уровней чистый на каждые сутки (ревью §0.7),"
    echo "# поэтому первый час суток недосчитывает стены, поставленные вчера, — для оси возраста это систематика"
    # H2 шаг 1 тем же прогоном: матрица + по-монетный разрез по порогам возраста {15…120} мин,
    # он же пишется csv рядом (сырьё — study/, не док).
    python3 bin/floors-balance.py "$out" --by-coin --min-per-day 10 --csv "study/floors-by-coin-$day.csv"
  } > "study/floors-$day.txt" 2>&1
  echo "== $(date -u +%FT%TZ) floors-balance $day → study/floors-$day.txt ($(wc -l < "study/floors-$day.txt") строк)" >> "$LOG"
  # S3 плана по сторонам: режим по минутам — медиана пула из mids1m-*.csv (S2) и BTC/ETH из справочных свечей.
  if ! regime_out=$(python3 bin/regime.py --day "$day" 2>&1); then
    alert "режим $day: $(echo "$regime_out" | tail -1 | cut -c1-200)"
  fi
  echo "== $(date -u +%FT%TZ) regime $day: $(echo "$regime_out" | tail -1 | cut -c1-200)" >> "$LOG"
}
echo "== $(date -u +%FT%TZ) nightly start; days in root: $(echo "$DAYS_ALL" | tr '\n' ' '); окно сеток: ${DAYS_WINDOW:-все сутки}; days-args:${DAY_ARGS:- нет}" >> "$LOG"
# Справочные свечи BTC/ETH (REST, задним числом, ~3 с) — до режима суток; сбой сети не роняет ночь.
if ! ref_out=$(python3 bin/ref-klines.py --out-dir study/regime 2>&1); then
  alert "ref-klines: $(echo "$ref_out" | tail -1 | cut -c1-200)"
fi
echo "== $(date -u +%FT%TZ) ref-klines: $(echo "$ref_out" | tail -2 | tr '\n' ' ' | cut -c1-200)" >> "$LOG"
for d in $DAYS_ALL; do
  touches_for_day "$d"
done
if [ -z "$TOUCHES_ONLY" ]; then
  # Семьи флоров В-71 и ось стороны (этап 1 дороги, side-axis-2026-09-19.md) — одним процессом; виды
  # (имена наборов) те же, что были у отдельных сеток, так что вердикты, журнал (маркеры
  # .trials-logged-<вид>) и читатели не меняются.
  # S0 плана по сторонам (side-plan-2026-09-20.md, 20.09): возраст по стороне — a15-s10/a30/a60 × bid|ask (192 испытания).
  # S1: состояние стены — eaten=<%> (усадка от максимума к касанию); пороги — квартили распределения по касаниям 16–18.09
  # (a45: q25 60 / q50 75; s100: q50 20 / q75 39), 6 наборов = 192 испытания.
  # S5: режим и растяжка по стороне — квартили по 16–18.09 (pool_ret_4h q25 −1.1 / q50 46.1 / q75 95.3;
  # btc_ret_4h q50 21.9; ret_1h касаний аск-стен a45 q75 66.4), 9 наборов = 288 испытаний; режим — study/regime.
  run_sets a15-s10-any:age=900,flow=10 a30-any:age=1800 a45-any:age=2700 a60-any:age=3600 s100-any:flow=100 \
           a45-bid:age=2700,side=bid a45-ask:age=2700,side=ask s100-bid:flow=100,side=bid s100-ask:flow=100,side=ask \
           a15-s10-bid:age=900,flow=10,side=bid a15-s10-ask:age=900,flow=10,side=ask \
           a30-bid:age=1800,side=bid a30-ask:age=1800,side=ask a60-bid:age=3600,side=bid a60-ask:age=3600,side=ask \
           a45-bid-e60:age=2700,side=bid,eaten=60 a45-bid-e75:age=2700,side=bid,eaten=75 \
           a45-ask-e60:age=2700,side=ask,eaten=60 a45-ask-e75:age=2700,side=ask,eaten=75 \
           s100-bid-e20:flow=100,side=bid,eaten=20 s100-bid-e39:flow=100,side=bid,eaten=39 \
           a45-bid-p4h-q50:age=2700,side=bid,pool4h_min=46.1 a45-bid-p4h-q75:age=2700,side=bid,pool4h_min=95.3 \
           a45-bid-p4h-neg:age=2700,side=bid,pool4h_max=0 a45-bid-b4h-q50:age=2700,side=bid,btc4h_min=21.9 \
           a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0 a45-ask-p4h-q25:age=2700,side=ask,pool4h_max=-1.1 \
           a45-ask-p4h-q50:age=2700,side=ask,pool4h_max=46.1 a45-ask-r1h-q75:age=2700,side=ask,ret1h_min=66.4 \
           a45-ask-both:age=2700,side=ask,pool4h_max=46.1,ret1h_min=66.4
  # E7 — другие формы и лот, поэтому свой процесс.
  run_one e7-a15-s10-any $USD $GRID --min-age-secs 900 --min-flow-pct 10 $E7 $DAY_ARGS
else
  echo "== $(date -u +%FT%TZ) TOUCHES_ONLY=1 — сетки пропущены намеренно (готовим касания для H2)" >> "$LOG"
fi
finish
