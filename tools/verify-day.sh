#!/usr/bin/env bash
# A2 (2026-09-17): сверка закрытых суток боевого корня.
#
# Зачем отдельный скрипт, а не одна строка в таймере: `lob verify` считает
# вердикт по **всем** частям символа в `--root`, а боевой корень держит много
# суток сразу (для 100 монет это сотни файлов). Если гнать verify по корню
# целиком, вердикт смешает дни и один маркер на символ скажет не то. Поэтому
# сутки раскладываются hardlink'ами в свой каталог (`/opt/alpha/verify/<день>`) —
# места не занимают, и по нему просто читать/анализировать; маркер после
# прогона копируется в боевой корень, где его ищут читатели (`profiles`, `watch`).
#
# Запуск: `/opt/alpha/tools/verify-day.sh [YYYY-MM-DD]` (по умолчанию — вчера
# по UTC). Таймер `alpha-verify.timer` дёргает его в 00:20 UTC: ротация суток
# идёт в 00:00 UTC, кадр на диск ложится не позже 10 с, двадцать минут — запас,
# чтобы вчерашние файлы точно были закрыты.
#
# Лог: `/opt/alpha/verify/<день>.log` — строка на часть, строка сводки на
# символ (`files=... violations=...` + вердикт) и итог `ok/fail/missing`.
# Код возврата всегда 0: `fail` — это вердикт о данных, а не сбой службы
# (иначе systemd красит юнит каждые сутки); настоящий сбой виден строкой
# `verify: day=... ` и отсутствием маркеров.

set -uo pipefail

ROOT="${ROOT:-/opt/alpha/root}"
ALPHA="${ALPHA:-/opt/alpha/alpha-verify}"
DAY="${1:-$(date -u -d 'yesterday' +%F)}"
WORK="${WORK:-/opt/alpha/verify/$DAY}"
LOG="${LOG:-/opt/alpha/verify/$DAY.log}"

if ! [[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "verify-day: день «$DAY» не в форме YYYY-MM-DD" >&2
    exit 2
fi
if [ ! -x "$ALPHA" ]; then
    echo "verify-day: бинарник $ALPHA не найден или не исполняемый" >&2
    exit 2
fi
if [ ! -f "$ROOT/instruments.csv" ]; then
    echo "verify-day: нет пула $ROOT/instruments.csv" >&2
    exit 2
fi

# Двое сразу (таймер и рука) — только лишняя нагрузка на тот же диск.
exec 9>/run/alpha-verify.lock
if ! flock -n 9; then
    echo "verify-day: другой прогон ещё идёт — выход"
    exit 0
fi

mkdir -p "$WORK" "$(dirname "$LOG")"
shopt -s nullglob
parts=("$ROOT"/*-"$DAY".binlog "$ROOT"/*-"$DAY"-p*.binlog \
       "$ROOT"/*-"$DAY".binlog.zst "$ROOT"/*-"$DAY"-p*.binlog.zst)
if [ "${#parts[@]}" -eq 0 ]; then
    echo "verify-day: за $DAY в $ROOT частей нет" >&2
    exit 2
fi
ln -f "${parts[@]}" "$WORK"/ 2>/dev/null || true

: >"$LOG"
echo "verify: day=$DAY root=$ROOT work=$WORK parts=${#parts[@]} started_utc=$(date -u +%FT%TZ)" | tee -a "$LOG"

ok=0
fail=0
missing=0
while IFS=, read -r symbol _rest; do
    symbol="${symbol%$'\r'}"
    [ -n "$symbol" ] || continue
    # Шапка CSV (`symbol,tick_size,...`) — не символ; комментарии в пуле тоже
    # возможны (`# debug` у отладочных прогонов).
    case "$symbol" in
        symbol | \#*) continue ;;
    esac

    have=("$WORK/$symbol-$DAY"*.binlog*)
    if [ "${#have[@]}" -eq 0 ]; then
        echo "verify: $symbol — частей за $DAY нет, символ пропущен" | tee -a "$LOG"
        missing=$((missing + 1))
        continue
    fi

    out="$("$ALPHA" lob verify --symbol "$symbol" --root "$WORK" 2>&1)"
    printf '%s\n' "$out" >>"$LOG"
    total="$(printf '%s\n' "$out" | grep '^verify: files=' | tail -1)"
    status="$(printf '%s\n' "$out" | sed -n 's/^verify: status=\([a-z]*\).*/\1/p' | tail -1)"

    marker="$WORK/verify-$symbol.status"
    if [ -f "$marker" ]; then
        # Маркер едет в боевой корень в любом случае: старый `ok` там не должен
        # пережить сутки, на которых символ стал `fail`.
        cp -f "$marker" "$ROOT/verify-$symbol.status"
    fi
    case "$status" in
        ok) ok=$((ok + 1)) ;;
        *) fail=$((fail + 1)) ;;
    esac
    printf 'verify: %s status=%s %s\n' "$symbol" "${status:-нет}" "${total:-сводки нет}" >>"$LOG"
done <"$ROOT/instruments.csv"

echo "verify: day=$DAY ok=$ok fail=$fail missing=$missing log=$LOG finished_utc=$(date -u +%FT%TZ)" | tee -a "$LOG"
exit 0
