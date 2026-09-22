#!/usr/bin/env bash
# Ночная сетка на счётной машине (владелец 2026-09-19: «со временем появится ещё день —
# как появится, так потестишь»): после ночного переноса суток (alpha-sync 00:45 UTC на
# коллекторе) гоняет базу В-65 на полах владельца (В-66/67, пол возраста В-70 = 15 мин),
# E7 (В-69), затем вердикты — по ВСЕМ суткам корня (без --day). Испытания каждого вида сетки
# регистрируются в журнале один раз (первая ночь, маркер study/.trials-logged-<вид>).
#
#   $ALPHA_HOME/bin/nightly-grid.sh                   # из alpha-grid-nightly.timer (02:00 UTC)
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
# Машина — окружением (L8, 2026-09-22): ALPHA_HOME (умолчание /opt/alpha-compute — VPS-счётная,
# root, системные юниты), на Steam Deck ALPHA_HOME=$HOME/alpha и пользовательские юниты (SC).
ALPHA_HOME="${ALPHA_HOME:-/opt/alpha-compute}"
cd "$ALPHA_HOME" || exit 1
# NIGHT_TAG — префикс меток сеток и их юнитов (умолчание nightly): прогон эпохи (epoch-run.sh) идёт
# со своим тегом, чтобы гейт «сетка ещё идёт» регулярной ночи не видел его юниты и наоборот.
# H3_JOBS — сколько монет касаний считать параллельно (умолчание 3).
export PATH=/root/.cargo/bin:$HOME/.cargo/bin:$PATH
SC=(); [ "$(id -u)" = 0 ] || SC=(--user)   # systemctl: без root — пользовательские юниты
DAY=$(date -u +%F)
LOG=study/nightly-$DAY.log
# Громкие ошибки (владелец 20.09: «почему если что-то падает — это молчаливо?»): каждая проблема —
# строка в study/ALERTS.log (дата, ночь, что именно), в конце ночи одна строка «ОК/ПРОВАЛ», и при
# любой проблеме скрипт выходит с кодом 1 — юнит alpha-grid-nightly виден в `systemctl --failed`.
# Внешний канал — ALERT_CMD (окружение юнита): команда получает текст первым аргументом, например
# curl к api.telegram.org с токеном из /etc/alpha/alert.env (файл root:600, кладёт владелец).
ALERTS=study/ALERTS.log
NALERTS=0
# Параллельные стадии (NIGHT_JOBS > 1) считают тревоги в своих подоболочках — итог ночи берётся из
# файла: строки этой ночи, дописанные после старта.
ALERTS_START=$( [ -f "$ALERTS" ] && wc -l < "$ALERTS" || echo 0 )
alert() {
  NALERTS=$((NALERTS + 1))
  echo "$(date -u +%FT%TZ) nightly-$DAY: $*" >> "$ALERTS"
  echo "!! $*" >> "$LOG"
}
# Чтение ночи TypeSafe (В-81): суждение «ок / смотреть / сломано» с причиной поверх фактов
# (ALERTS этой ночи, verdicts.csv, хвост лога, failed-юниты, диск) — строкой в ALERTS.log и в
# текст Telegram. Ключ — /etc/alpha/typesafe.env (drop-in юнита; для ручного пуска — source ниже).
# Статус и причину решает код (аудит дизайна 22.09 §1), модель добавляет только мнение с версией;
# без ключа строка пишется всё равно — с пометкой «мнение модели: нет ключа».
# На деке нет sudo — ключ лежит у пользователя: ~/.config/alpha/typesafe.env (0600).
for ts_env in /etc/alpha/typesafe.env "$HOME/.config/alpha/typesafe.env"; do
  if [ -f "$ts_env" ]; then set -a; . "$ts_env"; set +a; break; fi
done
read_night() {
  local line
  line=$(python3 "$ALPHA_HOME/bin/nightly-read.py" --night "$DAY" --study study 2>/dev/null | head -1)
  if [ -n "$line" ]; then echo "$line" >> "$ALERTS"; echo "== $line" >> "$LOG"; fi
  echo "$line"
}
finish() {
  NALERTS=$(tail -n +$((ALERTS_START + 1)) "$ALERTS" 2>/dev/null | grep -c "nightly-$DAY:" || true)
  local reading
  if [ "$NALERTS" -eq 0 ]; then
    echo "$(date -u +%FT%TZ) nightly-$DAY: ОК" >> "$ALERTS"
    echo "== $(date -u +%FT%TZ) nightly done: ОК" >> "$LOG"
    reading=$(read_night)
    [ -n "${ALERT_CMD:-}" ] && $ALERT_CMD "alpha nightly $DAY: ОК${reading:+; $(echo "$reading" | cut -d: -f4-)}" >/dev/null 2>&1
    exit 0
  fi
  echo "$(date -u +%FT%TZ) nightly-$DAY: ПРОВАЛ — проблем $NALERTS (выше)" >> "$ALERTS"
  echo "== $(date -u +%FT%TZ) nightly done: ПРОВАЛ — проблем $NALERTS, см. $ALERTS" >> "$LOG"
  reading=$(read_night)
  [ -n "${ALERT_CMD:-}" ] && $ALERT_CMD "alpha nightly $DAY: ПРОВАЛ — проблем $NALERTS: $(grep "nightly-$DAY:" "$ALERTS" | tail -n +1 | cut -d: -f4- | tr '\n' ';' | cut -c1-500)" >/dev/null 2>&1
  exit 1
}
BIN=$ALPHA_HOME/bin/alpha
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
# Гейт «сетка предыдущей ночи ещё идёт» — смотреть **только юниты сеток** (`run-grid.sh` создаёт
# `alpha-grid-<метка>`, метка ночного набора начинается с `nightly-`). Шаблон `alpha-grid-*` из
# первой редакции ловил и сам `alpha-grid-nightly.service` (этот скрипт: он в этот момент active/running),
# и таймер, поэтому **ночь объявляла занятой саму себя** и пропускалась: ручной пуск (не из юнита)
# работал, а таймерный — нет (найдено 21.09: ночи 20.09 и 21.09 02:00Z пропущены ровно так, см.
# `study/ALERTS.log`). Имена вырезаются из строки целиком (`grep -o`), потому что у упавших юнитов
# в начале строки стоит маркер `●`, и нумерация полей `awk` на них съезжает.
running_grids=$(systemctl "${SC[@]}" list-units "alpha-grid-${NIGHT_TAG:-nightly}-*" --no-legend \
  | grep -E ' (running|start) ' | grep -oE "alpha-grid-${NIGHT_TAG:-nightly}-[^ ]+\.service" | tr '\n' ' ')
if [ -n "$running_grids" ]; then
  echo "== $(date -u +%FT%TZ) сетка ещё идёт — ночной прогон пропущен ($running_grids)" >> "$LOG"
  alert "ночь пропущена: сетка ещё идёт ($running_grids)"
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
# Подходы (F10 этапа F, dev-plan-2026-09-20.md; замер M15 — approach-signal-2026-09-20.md):
# H3-шаг заодно пишет `approaches-<SYMBOL>.csv` рядом с касаниями. Полоса D = 20 bps —
# из замера (D=20: 276 подходов/сутки к стенам ≥ 45 мин, до касания 25,7 %, медиана 196 с;
# D=10 почти столько же, но ожидание вдвое короче — выбор между ними делает предрегистрация
# F10, значение переопределяется окружением). Пол возраста 900 с обязателен: без пола сигнал
# пишет миллионы записей в сутки ради 4–6 % касаний (5,5 ГБ на три прогона в замере).
APPROACH="--approach-bps ${APPROACH_BPS:-20} --approach-min-age-secs ${APPROACH_MIN_AGE_S:-900}"
# Только сеткам (у `lob touches` такого флага нет — 20.09 он по ошибке стоял в USD и ронял касания):
# Кэш касаний переопределяется окружением: наборам `--signal approach` нужен каталог
# `study/approaches/D<d>` (F1 пишет `approaches-<SYMBOL>.csv` **рядом** с `touches-<SYMBOL>.csv`;
# кэш D-полосы снимается `bin/approach-scan.sh`).
GRID="--touches-from ${TOUCHES_FROM:-study/touches} --regime-from ${REGIME_FROM:-study/regime}"
# Кэш касаний — единственный источник (22.09): он строится по маркерам K1 своих суток, а сетка без
# флага при неполном кэше уходила в реплей всех суток корня по маркеру последних суток (LSK: сверка
# `ok` только за 21.09) — ~4 ГБ на процесс, OOM. Сутки без кэша теперь пропускаются.
GRID="$GRID --touches-cache-only"
# Форма выхода F7/F8 (Б-75): умолчание ночи — `none`, то есть прежний круг и гейт
# «те же круги»; наборы предрегистрации F10 включаются окружением, например
# EXIT_FORMS="none eat50 gone50" — форма выхода становится осью сетки (имена форм
# несут хвост `-eat50`/`-gone20`, колонки `n_eaten_by_trades`/`n_wall_gone`).
# Числа X/W — предрегистрация (умолчаний в коде нет), поэтому список пуст.
for x in ${EXIT_FORMS:-none}; do GRID="$GRID --exit-form $x"; done
# Сигнал и вход F6/F5 (этап F, F10) — те же оси, что у выхода: умолчания ночи —
# `touch` (сигнал касания), `single@fr` (одиночная нога у фронтранера) и, с 22.09 (В-85 п. 6),
# срок жизни входа `wall` с полосой 20 bps вместо `touch` (тот знал конец касания заранее);
# гейт «те же круги» — ENTRY_TTL=touch явно. Наборы предрегистрации включаются окружением, например:
#   SIGNAL=approach TOUCHES_FROM=study/approaches/D20 \
#   ENTRY_FORMS="ladder3x2..10" ENTRY_TTL="300 1800" BAND_EXIT_BPS=20
# `ENTRY_TTL` — список значений через пробел (`touch|wall|<секунды>`), остальные —
# тоже списки (декартово произведение даёт номерные формы). Условия F5 требуют
# порога В-66 — он есть в `$USD` (`--h3-usd`); без него набор откажет на старте.
# Числа D/ttl/полосы — предрегистрация F10, умолчаний в коде нет.
for x in ${SIGNAL:-touch}; do GRID="$GRID --signal $x"; done
for x in ${ENTRY_FORMS:-single@fr}; do GRID="$GRID --entry-form $x"; done
# Срок жизни входа: умолчание `wall` (живёт, пока стена жива, В-74) с полосой ухода 20 bps (В-80) —
# прежнее `touch` берёт длительность касания, известную только задним числом (аудит дизайна 22.09
# §5 Т1, В-85 п. 6): `touch` оставлен только для гейтов регрессии (ENTRY_TTL=touch явно).
for x in ${ENTRY_TTL:-wall}; do GRID="$GRID --entry-ttl-secs $x"; done
if [ -n "${BAND_EXIT_BPS:-}" ]; then
  for x in $BAND_EXIT_BPS; do GRID="$GRID --band-exit-bps $x"; done
elif [ "${ENTRY_TTL:-wall}" != "touch" ]; then
  GRID="$GRID --band-exit-bps 20"
fi
# Выход по «прилипанию» (B4, В-58 п. 5; ось сетки — В-85 п. 5): EARLY_EXIT="off 1 2 3".
for x in ${EARLY_EXIT:-}; do GRID="$GRID --early-exit-secs $x"; done
# Модель очереди F3: у `bounce-grid` флаг `--queue-model` **обязательный**, умолчания
# в коде нет. Ночь идёт через `run-grid.sh`, который подставляет `risk-adverse`, —
# здесь то же значение ставится явно и экспортируется: иначе ночь упадёт, если
# умолчание в скрипте когда-нибудь поменяют. Наборы F10 с моделью по объёму
# включаются `QUEUE_MODEL=prob:<n>` (число — предрегистрация).
QUEUE_MODEL="${QUEUE_MODEL:-risk-adverse}"
export QUEUE_MODEL
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
# Журнал испытаний пишется один раз на (вид, конфигурацию осей): тег — хеш строки осей и форм.
# Раньше маркер был на вид, и смена осей (ttl `wall` вместо `touch`, ось «прилипания») не
# доложила бы новые формы в runs.csv — DSR считал бы меньше испытаний, чем было (аудит 22.09 Т10).
TRIALS_TAG=$(echo "$GRID $BASE ${FORMS:-} ${EARLY_EXIT:-}" | md5sum | cut -c1-8)
E7="--stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form half1to1 --take-form eat50x80 --order-qty-mult 2"
# Испытания регистрируются в журнале один раз на вид сетки (первая ночь) — дальше формы те же.
verdict_one() {
  # $1 — вид (имя набора/сетки), $2 — каталог сетки; вердикт study/bounce-verdict-nightly-<день>-<вид>.csv
  local kind=$1 gdir=$2
  local label="${NIGHT_TAG:-nightly}-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind-$TRIALS_TAG" ]; then logflag="--log-trials"; fi
  # Вердикты параллельных стадий дописывают журнал испытаний ($RUNS) — по очереди, под замком.
  if flock "study/.verdict.lock" $BIN lob bounce-verdict --grid-dir "$gdir" --runs-csv "$RUNS" --out "study/bounce-verdict-$label.csv" $logflag > "study/bounce-verdict-$label.log" 2>&1; then
    [ -n "$logflag" ] && touch "study/.trials-logged-$kind-$TRIALS_TAG"
  else
    alert "вердикт $label не посчитался: $(tail -2 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-200)"
  fi
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
  # Реестр вердиктов (docs/plan/EXPERIMENTS.md): строка на (ночь, вид) — итог, лучшая форма, круги, точка, нижняя.
  [ -f study/verdicts.csv ] || echo "night,kind,verdict,form,rounds,point_bps,lower_bps" > study/verdicts.csv
  l=$(grep -a "ИТОГ" "study/bounce-verdict-$label.log" | tail -1)
  # Поля — `sed`, не `cut -c`: тот режет по байтам, и у русских меток («ИТОГ=», «лучшая »)
  # оставались мусорные префиксы во всех колонках (реестр до 22.09 починен nightly-read/repair).
  echo "$DAY,$kind,$(echo "$l" | grep -o "ИТОГ=[^·]*" | sed 's/^ИТОГ=//; s/ *$//'),$(echo "$l" | grep -o "лучшая [^ ]*" | sed 's/^лучшая //'),$(echo "$l" | grep -o "кругов [0-9]*" | sed 's/^кругов //'),$(echo "$l" | grep -o "точка=[-0-9.]*" | sed 's/^точка=//'),$(echo "$l" | grep -o "нижняя=[-0-9.]*" | sed 's/^нижняя=//')" >> study/verdicts.csv
  # Контроль «рост рынка» (аудит дизайна 22.09 §4 С1, В-85 п. 2): превышение каждой формы над
  # «той же позицией на той же монете в тот же день на том же удержании в случайную минуту».
  # Сводка — study/placebo-<метка>.csv, лучшая по превышению — строкой в лог ночи.
  if python3 "$ALPHA_HOME/bin/placebo.py" --grid-dir "$gdir" --mids study/touches \
      --csv "study/placebo-$label.csv" > "study/placebo-$label.log" 2>&1; then
    echo "== $(date -u +%FT%TZ) контроль $label: $(sed -n 2p "study/placebo-$label.log" | cut -c1-300)" >> "$LOG"
  else
    alert "контроль $label не посчитался: $(tail -1 "study/placebo-$label.log" | cut -c1-200)"
  fi
}
# Все наборы базы одним процессом (`--set`, 20.09): события суток и окна декодируются один раз на
# монету, а не по разу на семью — девять сеток стоят как одна; артефакты b5/nightly-<день>-<метка>/<набор>/.
# FORMS — формы процесса (умолчание BASE), LABEL — метка каталога (умолчание base).
run_sets() {
  local label="${NIGHT_TAG:-nightly}-$DAY-${LABEL:-base}"
  local setargs=""
  for kv in "$@"; do setargs="$setargs --set $kv"; done
  echo "== $(date -u +%FT%TZ) grid $label start: наборы $*" >> "$LOG"
  THREADS=${GRID_THREADS:-3} "$ALPHA_HOME/bin/run-grid.sh" "$label" $USD $GRID ${FORMS:-$BASE} $DAY_ARGS $setargs >> "$LOG" 2>&1
  sleep 5
  while systemctl "${SC[@]}" is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  grid_check "$label"
  for kv in "$@"; do verdict_one "${kv%%:*}" "b5/$label/${kv%%:*}"; done
}
run_one() {
  local kind=$1; shift
  local label="${NIGHT_TAG:-nightly}-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind-$TRIALS_TAG" ]; then logflag="--log-trials"; fi
  echo "== $(date -u +%FT%TZ) grid $label start" >> "$LOG"
  THREADS=${GRID_THREADS:-3} "$ALPHA_HOME/bin/run-grid.sh" "$label" "$@" >> "$LOG" 2>&1
  sleep 5
  while systemctl "${SC[@]}" is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  grid_check "$label"
  verdict_one "$kind" "b5/$label"
}
# Сетка обязана кончиться штатно: юнит не failed, в grid.err строки «готов», нет «error»/panic.
grid_check() {
  local label=$1
  if systemctl "${SC[@]}" is-failed --quiet "alpha-grid-$label"; then
    alert "сетка $label: юнит failed — $(journalctl "${SC[@]}" -u "alpha-grid-$label" --no-pager -n 3 2>/dev/null | tail -1 | cut -c1-200)"
    systemctl "${SC[@]}" reset-failed "alpha-grid-$label" 2>/dev/null
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
           root/instruments.csv root/session.json; do
    [ -e "$f" ] || continue
    ln -s "$PWD/$f" "$dir/$(basename "$f")"
  done
  # Маркеры K1 — **за эти сутки** (22.09): `root/verify-*.status` — один файл на символ, его
  # перезаписывает каждая сверка, то есть это маркеры ПОСЛЕДНИХ сверенных суток, не этих.
  # Сверка пишет построчный вердикт в `/opt/alpha/verify/<день>.log` (`verify: <SYM> status=ok|fail`),
  # синк кладёт его в `root/verify-logs/<день>.log` — из него и строятся маркеры суток. Нет лога
  # (сутки до 18.09 или синк не донёс) — прежнее поведение: текущие маркеры, с пометкой в лог.
  local vlog="root/verify-logs/$day.log"
  if [ -f "$vlog" ]; then
    # Строка лога: `verify: <SYM> status=ok verify: files=1 …` — берём ровно поле статуса,
    # иначе маркер несёт хвост строки и сравнение с «ok» не проходит (поймано 22.09: 0 монет).
    grep -aE '^verify: [A-Z0-9]+ status=(ok|fail)' "$vlog" | while read -r _ sym st _; do
      echo "${st#status=}" > "$dir/verify-$sym.status"
    done
    echo "== $(date -u +%FT%TZ) day_root $day: маркеры из $vlog ($(ls "$dir"/verify-*.status 2>/dev/null | wc -l) символов)" >> "$LOG"
  else
    for f in "root/verify-"*".status"; do
      [ -e "$f" ] || continue
      ln -s "$PWD/$f" "$dir/$(basename "$f")"
    done
    echo "== $(date -u +%FT%TZ) day_root $day: лога сверки нет — маркеры текущие (последних сверенных суток)" >> "$LOG"
  fi
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
  xargs -r -P "${H3_JOBS:-3}" -I{} -a "$out/symbols.txt" nice -n 15 bash -c \
    "$BIN lob touches --root 'study/root-$day' --symbol {} $USD $APPROACH --out '$out/touches-{}.csv' >'$out/{}.log' 2>&1 || echo {} >> '$out/failed.txt'"
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
# H3_DAYS — посчитать касания только этих суток (конвейер эпохи: сутки, загрузка которых кончилась).
for d in ${H3_DAYS:-$DAYS_ALL}; do
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
  # S8 (20.09, «развивать отскоки»): возраст 90/120 мин, вход только от фронтрана, размер стены ≥ $25k/$50k — лонги;
  # 5 наборов = 160 испытаний.
  BASE_SETS="a15-s10-any:age=900,flow=10 a30-any:age=1800 a45-any:age=2700 a60-any:age=3600 s100-any:flow=100 \
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
           a45-ask-both:age=2700,side=ask,pool4h_max=46.1,ret1h_min=66.4 \
           a90-bid:age=5400,side=bid a120-bid:age=7200,side=bid a45-bid-fr:age=2700,side=bid,frontrun \
           a45-bid-u25:age=2700,side=bid,usd_min=25000 a45-bid-u50:age=2700,side=bid,usd_min=50000"
  # Параллельные стадии (дек, 22.09): процесс сетки почти весь однопоточный — декод событий монеты-суток
  # идёт последовательно, потоки `--threads` работают только на формах. На 8 ядрах дека одна сетка
  # грузила ~1 ядро (замер: нагрузка 45 %, у процесса 1 поток). NIGHT_JOBS > 1 — стадии (база, tk, dl,
  # E7, скальп, OOS) идут одновременно, BASE_SPLIT = N — база делится на N процессов по наборам (каждый
  # декодирует события сам, зато параллельно; виды и вердикты те же, каталоги b5/nightly-<день>-base-<i>/).
  # Умолчания 1/1 — прежняя последовательная ночь (VPS-счётная: 4 vCPU и соседи).
  # NIGHT_JOBS — не больше стольких стадий сразу (семафор по фоновым заданиям): 22.09 все семь стадий
  # сразу по 2–3 ГБ каждая переросли 14.8 ГБ дека — своп, нагрузка 51, ssh не отвечал, три стадии
  # убиты по памяти. Память, а не ядра, — предел параллельности на деке.
  stage() {
    if [ "${NIGHT_JOBS:-1}" -gt 1 ]; then
      while [ "$(jobs -rp | wc -l)" -ge "${NIGHT_JOBS}" ]; do wait -n; done
      "$@" &
    else
      "$@"
    fi
  }
  split_n="${BASE_SPLIT:-1}"
  if [ "$split_n" -le 1 ]; then
    stage run_sets $BASE_SETS
  else
    i=0
    # shellcheck disable=SC2206
    all=($BASE_SETS)
    chunk=$(( (${#all[@]} + split_n - 1) / split_n ))
    while [ $((i * chunk)) -lt ${#all[@]} ]; do
      LABEL="base-$((i + 1))" stage run_sets "${all[@]:$((i * chunk)):$chunk}"
      i=$((i + 1))
    done
  fi
  # S8 тейк в % (tk<x>, 20.09): смоук на 5 монетах — ближний тейк режет хвост часа (+$120 → tk1 +$64 → tk0.5 +$27);
  # одна регистрация на всём пуле, чтобы закрыть ось честно; 3 набора × 64 формы = 192 испытания.
  LABEL=tk FORMS="--stop-form pct1 --stop-form pct2 --take-form tk0.5 --take-form tk1" \
    stage run_sets tk-a45-bid:age=2700,side=bid tk-a45-bid-p4h-neg:age=2700,side=bid,pool4h_max=0 tk-a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0
  # S8 удержание (--deadline-secs, 20.09): 30 мин и 4 ч рядом с базовыми; стопы pct1/pct2, тейк 1:1 — 12 форм × 3 набора = 36.
  LABEL=dl FORMS="--stop-form pct1 --stop-form pct2 --take-form 1to1 --deadline-secs 60 --deadline-secs 600 --deadline-secs 1800 --deadline-secs 3600 --deadline-secs 7200 --deadline-secs 14400" \
    stage run_sets dl-a45-bid:age=2700,side=bid dl-a45-bid-p4h-neg:age=2700,side=bid,pool4h_max=0 dl-a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0
  # E7 — другие формы и лот, поэтому свой процесс.
  stage run_one e7-a15-s10-any $USD $GRID --min-age-secs 900 --min-flow-pct 10 $E7 $DAY_ARGS
  # Скальп-отскок практиков отдельно от «дрейфа от стены» (аудит дизайна 22.09 §2, В-85 п. 4–5):
  # минуты, стоп у стены (at/behind/stack2 — В-65; before и midfr при входе у фронтранера вырождены — 0 сигналов), тейк 1:1, дедлайны 60/600 с (В-38) и выход по
  # «прилипанию» off/1/2/3 с (В-58 п. 5) — главное правило S/D/T, до 22.09 в сетке выключенное.
  # 3 стопа × 2 дедлайна × 4 = 24 формы × 4 набора = 96 испытаний (prereg в runs.csv 22.09).
  LABEL=scalp FORMS="--stop-form at --stop-form behind --stop-form stack2 --take-form 1to1 --deadline-secs 60 --deadline-secs 600 --early-exit-secs off --early-exit-secs 1 --early-exit-secs 2 --early-exit-secs 3" \
    stage run_sets scalp-a45-bid:age=2700,side=bid scalp-a45-ask:age=2700,side=ask scalp-s100-bid:flow=100,side=bid scalp-s100-ask:flow=100,side=ask
  # Замороженная живая ветка F10 — out-of-sample с 23.09 (В-85 п. 3): новые сутки → кэш подходов D20,
  # замороженная форма, склейка, вердикт и контроль; журнал study/oos-frozen.log, итог — в лог ночи.
  oos() {
    if ! ALPHA_HOME="$ALPHA_HOME" GRID_THREADS="${GRID_THREADS:-3}" RUNS="$RUNS" "$ALPHA_HOME/bin/oos-frozen.sh" >> "$LOG" 2>&1; then
      alert "oos-frozen: завершился с ошибкой — study/oos-frozen.log"
    fi
  }
  stage oos
  wait
else
  echo "== $(date -u +%FT%TZ) TOUCHES_ONLY=1 — сетки пропущены намеренно (готовим касания для H2)" >> "$LOG"
fi
finish
