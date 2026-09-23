#!/usr/bin/env bash
# G11 (В-88): стресс-тест кандидата на обвале 10–11.10.2025 — отдельная эпоха из архива Bybit.
#   BIN=bin/alpha-<хеш> crash-stress.sh [сутки…]        # умолчание 2025-10-10 2025-10-11
# Дом эпохи — ~/alpha/epochs/e-crash (root/, study/, b5/, bin → ~/alpha/bin), как у «истории» (epoch-run.sh),
# но без ночного набора сеток: импорт → касания и режим суток → кандидат → b5/crash/<сутки>/<набор>/rounds.csv.
# Кандидат (CLAUDE.md «СОСТОЯНИЕ»): вход замороженный F10; наборы — база лонга и просадки BTC 1 ч / 4 ч (края v1);
# выход стоп 2 % × {трейл +1 %/откат 1 %, ранний трейл 0.5/0.25} × удержание 4 ч, на стену не реагировать.
# Идемпотентно: импортированные монеты-сутки и посчитанные сутки кандидата не пересчитываются.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
HOME_E="${CRASH_HOME:-$A/epochs/e-crash}"
BIN="${BIN:?бинарник — BIN=bin/alpha-<хеш>}"
DAYS=("$@"); [ ${#DAYS[@]} -gt 0 ] || DAYS=(2025-10-10 2025-10-11)
mkdir -p "$HOME_E"/{root,study/regime,b5}
LOG="$HOME_E/crash-stress.log"
say() { echo "== $(date -u +%FT%TZ) crash-stress: $*" | tee -a "$LOG"; }
[ -e "$HOME_E/bin" ] || ln -s "$A/bin" "$HOME_E/bin"
[ -f "$HOME_E/root/instruments.csv" ] || cp "$A/root/instruments.csv" "$HOME_E/root/"
# Импорт архива session.json не пишет, а `bounce-grid` без него каталог сессией не считает (как в epoch-run.sh).
[ -f "$HOME_E/root/session.json" ] || echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$HOME_E/root/session.json"
[ -f "$HOME_E/study/runs-2026-09-19.csv" ] || cp "$A/study/runs-2026-09-19.csv" "$HOME_E/study/"

# Минутные BTC/ETH для режима: с суток до первых (окно 4 ч захватывает прошлые сутки).
since=$(date -u -d "${DAYS[0]} -1 day" +%F)
python3 "$A/bin/ref-klines.py" --out-dir "$HOME_E/study/regime" --since "$since" --until "${DAYS[-1]}" 2>&1 | tail -2 >> "$LOG"
say "импорт ${DAYS[*]}"
ALPHA_HOME="$A" JOBS="${IMPORT_JOBS:-6}" "$A/bin/archive-import.sh" "$HOME_E/root" "${DAYS[@]}" >> "$LOG" 2>&1
for d in "${DAYS[@]}"; do
  say "$d сверка: $(grep -oE 'status=[a-z]+' "$HOME_E/root/verify-logs/$d.log" | sort | uniq -c | tr -s ' \n' ' ')"
done
say "касания и режим суток"
ALPHA_HOME="$HOME_E" NIGHT_TAG=e-crash-h3 TOUCHES_ONLY=1 H3_DAYS="${DAYS[*]}" "$A/bin/nightly-grid.sh" >> "$LOG" 2>&1

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }
FORM="--stop-form pct2 --take-form tr1x1 --take-form tr0.5x0.25 --deadline-secs 14400 --exit-form none"
say "кандидат: 2 формы × 3 набора, $BIN"
ALPHA_HOME="$HOME_E" FROM_DAY="${DAYS[0]}" OOS_DIR=b5/crash SETS="$SETS" BIN="$BIN" FORM_EXIT="$FORM" \
  FORM_NAME=all RUNS=study/runs-2026-09-19.csv GRID_THREADS="${GRID_THREADS:-2}" DAY_JOBS="${DAY_JOBS:-2}" \
  "$A/bin/oos-frozen.sh" >> "$LOG" 2>&1
say "готово → $HOME_E/b5/crash"
