#!/usr/bin/env bash
# Замороженная живая ветка F10 — первый честный out-of-sample (аудит дизайна 22.09 §7 п. 8, В-85 п. 3).
#
# Форма и наборы заморожены 2026-09-22 (строки `prereg` в docs/plan/runs.csv) и больше не меняются:
#   вход на подходе D = 20 bps (M15), лестница ladder3x2..20w2 (M16), срок жизни входа 1800 с и
#   полоса ухода 20 bps (В-80), стоп pct2 и тейк 1to1 (В-65), дедлайн 7200 с (В-38), выход none,
#   модель очереди prob:3 (В-80), порог номинала $10k (В-66);
#   наборы a45-bid (age=2700, side=bid — главная) и a45-bid-b4h-neg (+ btc4h_max=0 — гипотеза H1).
# Читаются только сутки начиная с FROM_DAY (умолчание 2026-09-23 — день после заморозки). Форму
# выбирали на 16–20.09; 21–22.09 при выборе не читались, но в официальный OOS не входят (заморозка
# 22.09) — их можно посчитать проверочным прогоном в другой каталог: FROM_DAY=2026-09-21
# OOS_DIR=b5/check-0921 (склейка берёт только сутки ≥ FROM_DAY своего каталога).
#
# Шаги на каждые новые сутки ≥ FROM_DAY, у которых ночь уже собрала корень-день и касания:
#   1) кэш подходов D20 этих суток (approach-scan.sh), метка .done;
#   2) замороженная форма по корню-дню → b5/oos-frozen/<сутки>/<набор>/;
# затем склейка всех OOS-суток → b5/oos-frozen/merged/<набор>/, вердикт
# (study/bounce-verdict-oos-frozen-<набор>.csv) и контроль «рост рынка» (study/placebo-oos-frozen-<набор>.csv).
# Идемпотентно: посчитанные сутки не пересчитываются. Вызывается ночью после сеток (nightly-grid.sh).
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_env.sh
source "$SELF_DIR/_env.sh"
ALPHA_HOME="${ALPHA_HOME:-/opt/alpha-compute}"
cd "$ALPHA_HOME" || exit 1
FROM_DAY="${FROM_DAY:-2026-09-23}"
BIN="${BIN:-bin/alpha}"
THREADS="${GRID_THREADS:-3}"
RUNS="${RUNS:-$RUNS_JOURNAL}"
OOS_DIR="${OOS_DIR:-b5/oos-frozen}"   # другой каталог — проверочный прогон вне официального OOS
TAG="$(basename "$OOS_DIR")"
LOG="${LOG:-study/$TAG.log}"
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
# ORDER — размер позиции. Умолчание — минимальный лот биржи (22а, ~$5): так считались все прогоны до 23.09, и
# доллары в отчётах были процентом × $1000, а не исполнением на $1000. `ORDER="--order-usd 500"` (владелец 23.09:
# «объём позиции 500, полный») — вся лестница на $500, очередь и выход исполняют реальный размер.
ORDER="${ORDER:---order-qty-from-pool}"
# CARRY — позиция, открытая к концу суток UTC, досчитывается на следующих сутках из root/ (исправление 23.09: без
# него она выпадала из итога — 35 символо-суток на 189 сделок кандидата). Пусто — прежнее поведение.
CARRY="${CARRY---carry-root root}"
FORM="--signal approach --queue-model prob:3 $RTT --regime-from study/regime $ORDER $CARRY \
  --h3-mode notional --h3-usd 10000 --entry-form ladder3x2..20w2 --entry-ttl-secs 1800 --band-exit-bps 20 \
  ${FORM_EXIT:---stop-form pct2 --take-form 1to1 --deadline-secs 7200 --exit-form none}"
# FORM_EXIT — титрование выхода (план 2026-09-23, G9): вход тот же замороженный, выход — сетка
# (`--stop-form`/`--take-form`/`--deadline-secs`/`--exit-form` повторами). Тогда FORM_NAME=all: контроль
# рынка печатается по каждой форме. Для замороженного OOS не задаётся.
SETS_FROZEN="a45-bid:age=2700,side=bid a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0"
# SETS — другие наборы поверх той же замороженной формы (титрование, план 2026-09-23 G4/G5); форма не
# меняется. Для замороженного OOS не задаётся. VERDICT_FLAGS — `--log-trials` у титрования: каждая
# корзина — испытание, журнал считает их для DSR.
SETS="${SETS:-$SETS_FROZEN}"
FIRST_SET="${SETS%%:*}"
VERDICT_FLAGS="${VERDICT_FLAGS:-}"
FORM_NAME="${FORM_NAME:-ladder3x2..20w2-pct2-1to1-7200-ttl1800}"
PLACEBO_FORM=(--form "$FORM_NAME"); [ "$FORM_NAME" = all ] && PLACEBO_FORM=()
say() { echo "== $(date -u +%FT%TZ) oos-frozen: $*" | tee -a "$LOG"; }

# Параллельность (владелец 23.09: «сделай больше параллельности»): сутки независимы, поэтому считаются
# по DAY_JOBS разом, в кэше подходов суток — по SCAN_JOBS монет. Замер на деке 23.09: подходы суток шли
# 6 мин на 2 монетах из 8 ядер, форма — 3 мин, загрузка дека ~25 %; процесс кэша — 30–60 МБ.
# На 4 ядрах (VPS) — прежний последовательный ход.
NPROC=$(nproc 2>/dev/null || echo 4)
DAY_JOBS="${DAY_JOBS:-$(( NPROC >= 8 ? 3 : 1 ))}"
SCAN_JOBS="${SCAN_JOBS:-$(( NPROC >= 8 ? 3 : THREADS ))}"
mkdir -p "$OOS_DIR"
setargs=""; for s in $SETS; do setargs="$setargs --set $s"; done

one_day() {
  local day=$1
  local out="$OOS_DIR/$day"
  # Замок суток на весь дом (не на каталог прогона): кэш подходов общий у замороженного OOS, эпохи и
  # титрования — другой экземпляр, считающий те же сутки, дожидается, а не пишет вдвоём в одни файлы.
  exec 9>"study/.day-lock-$day"
  flock 9
  if [ ! -f "study/approaches/D20/$day/.done" ]; then
    say "$day: кэш подходов D20"
    # OUT_BASE — переменная approach-scan.sh (каталог кэша подходов): задаётся явно, иначе чужое окружение
    # уводит кэш не туда (поймано 22.09 проверочным прогоном).
    ALPHA_HOME="$ALPHA_HOME" JOBS="$SCAN_JOBS" OUT_BASE=study/approaches bin/approach-scan.sh 20 "$day" >> "$LOG" 2>&1 \
      && mkdir -p "study/approaches/D20/$day" && touch "study/approaches/D20/$day/.done"
  fi
  [ -f "$out/$FIRST_SET/forms.csv" ] && return 0
  say "$day: замороженная форма"
  # shellcheck disable=SC2086
  nice -n 15 $BIN lob bounce-grid --root "study/root-$day" --touches-from study/approaches/D20 \
    $FORM $setargs --threads "$THREADS" --out-dir "$out" > "$out.log" 2>&1 || {
    say "$day: ОШИБКА — $(tail -1 "$out.log" | cut -c1-200)"
    # Упавший прогон оставляет шапку forms.csv — без удаления сутки считались бы готовыми с нулём
    # сделок и больше не пересчитывались (23.09: архив 01–04 после сбоя session.json).
    rm -rf "$out"
  }
}

days=$(ls -d study/root-20??-??-?? 2>/dev/null | sed 's|study/root-||' | sort)
new=0
for day in $days; do
  [[ "$day" < "$FROM_DAY" ]] && continue
  [ -f "study/touches/$day/symbols.txt" ] || { say "$day: нет касаний суток — ждём ночь"; continue; }
  [ -f "study/regime/$day.csv" ] || { say "$day: нет режима суток — ждём ночь"; continue; }
  [ -f "study/approaches/D20/$day/.done" ] && [ -f "$OOS_DIR/$day/$FIRST_SET/forms.csv" ] && continue
  while [ "$(jobs -rp | wc -l)" -ge "$DAY_JOBS" ]; do wait -n; done
  one_day "$day" &
  new=$((new + 1))
done
wait
# NO_MERGE — помощник: досчитывает сутки параллельно основному прогону того же каталога (замки суток
# не дают считать вдвоём), склейку, вердикт и журнал испытаний оставляет основному.
if [ -n "${NO_MERGE:-}" ]; then say "сутки досчитаны без склейки (NO_MERGE, новых $new)"; exit 0; fi

for s in $SETS; do
  set_name="${s%%:*}"
  # Склеиваются только сутки ≥ FROM_DAY — прогон другого окна в том же каталоге OOS не загрязнит.
  parts=$(for d in $(ls -d "$OOS_DIR"/20??-??-?? 2>/dev/null | sort); do
    [[ "$(basename "$d")" < "$FROM_DAY" ]] && continue
    [ -d "$d/$set_name" ] && echo "$d/$set_name"
  done)
  [ -n "$parts" ] || { say "$set_name: OOS-суток ещё нет"; continue; }
  m="$OOS_DIR/merged/$set_name"; mkdir -p "$m"
  first=$(echo "$parts" | head -1)
  for f in forms.csv rounds.csv; do
    { grep -a '^#' "$first/$f"; grep -av '^#' "$first/$f" | head -1
      for p in $parts; do grep -av '^#' "$p/$f" | tail -n +2; done; } > "$m/$f"
  done
  n_days=$(echo "$parts" | wc -l)
  n_rounds=$(grep -avc '^#' "$m/rounds.csv"); n_rounds=$((n_rounds - 1))
  # Замок журнала испытаний — тот же, что у параллельных стадий nightly-grid.sh (study/.verdict.lock):
  # вердикт с --log-trials дописывает общий $RUNS, а oos-frozen.sh может идти вместе с ночью или другим
  # титрованием того же дома одновременно (2026-09-23).
  # shellcheck disable=SC2086
  flock "study/.verdict.lock" nice -n 10 $BIN lob bounce-verdict --grid-dir "$m" --runs-csv "$RUNS" $VERDICT_FLAGS \
    --out "study/bounce-verdict-$TAG-$set_name.csv" > "study/bounce-verdict-$TAG-$set_name.log" 2>&1
  python3 bin/placebo.py --grid-dir "$m" "${PLACEBO_FORM[@]}" --mids study/touches \
    --csv "study/placebo-$TAG-$set_name.csv" > "study/placebo-$TAG-$set_name.log" 2>&1
  say "$set_name: OOS-суток $n_days, сделок $n_rounds; $(grep -a ИТОГ "study/bounce-verdict-$TAG-$set_name.log" | tail -1 | sed 's/^bounce-verdict: //' | cut -c1-200); контроль: $(sed -n 2p "study/placebo-$TAG-$set_name.log" | cut -c1-200)"
done
say "готово (новых суток $new)"
