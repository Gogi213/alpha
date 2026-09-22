#!/usr/bin/env bash
# Прогон эпохи (владелец 22.09: «две эпохи — история из архива за текущий месяц кроме заколлекченных
# суток и наша запись; два прогона»). Одна и та же конфигурация на обеих эпохах:
#   1) ночной набор (nightly-grid.sh: касания по суткам, флоры, режим, сетки базы/tk/dl/E7/скальп,
#      вердикты, контроль «рост рынка») — со своим тегом меток (NIGHT_TAG), чтобы не мешать ночи;
#   2) замороженная живая ветка F10 (oos-frozen.sh) на всех сутках эпохи — вердикт и контроль.
#
#   epoch-run.sh archive <дом эпохи> <сутки…>   # эпоха «история»: импорт архива Bybit, затем прогон
#   epoch-run.sh collected                        # эпоха «запись»: прогон по ~/alpha (наши сутки)
#
# Дом эпохи «история» — отдельный каталог (root/, study/, b5/, bin → ~/alpha/bin): её сутки не
# смешиваются с нашей записью. Пул — тот же instruments.csv, что у коллектора.
set -uo pipefail
MODE="${1:?archive|collected}"; shift
BASE="${ALPHA_BASE:-$HOME/alpha}"
export GRID_THREADS="${GRID_THREADS:-2}" GRID_MEM="${GRID_MEM:-4G}" GRID_SLICE="${GRID_SLICE:-alpha.slice}"
export NIGHT_JOBS="${NIGHT_JOBS:-3}" BASE_SPLIT="${BASE_SPLIT:-2}" H3_JOBS="${H3_JOBS:-6}"

if [ "$MODE" = archive ]; then
  HOME_E="${1:?дом эпохи}"; shift
  DAYS=("$@"); [ ${#DAYS[@]} -gt 0 ] || { echo "нужны сутки"; exit 1; }
  mkdir -p "$HOME_E"/{root,study/regime,b5}
  [ -e "$HOME_E/bin" ] || ln -s "$BASE/bin" "$HOME_E/bin"
  [ -f "$HOME_E/root/instruments.csv" ] || cp "$BASE/root/instruments.csv" "$HOME_E/root/"
  # Журнал испытаний — копия общего: DSR эпохи считает и прежние испытания, и её собственные.
  [ -f "$HOME_E/study/runs-2026-09-19.csv" ] || cp "$BASE/study/runs-2026-09-19.csv" "$HOME_E/study/"
  # Минутные BTC/ETH для режима: с суток до первых (окно 4 ч захватывает прошлые сутки).
  since=$(date -u -d "${DAYS[0]} -1 day" +%F)
  python3 "$BASE/bin/ref-klines.py" --out-dir "$HOME_E/study/regime" --since "$since" --until "${DAYS[-1]}" | tail -2
  FROM="${DAYS[0]}"
  TAG=e-archive
  # Конвейер (22.09): загрузка упирается в сеть (7 МБ/с по Wi-Fi), процессор простаивает — поэтому
  # сутки, загрузка которых кончилась (у каждой монеты пула есть строка сверки), сразу идут в
  # касания (TOUCHES_ONLY, H3_DAYS) и в замороженную F10, а не ждут все сутки эпохи.
  echo "== $(date -u +%FT%TZ) эпоха archive: импорт ${#DAYS[@]} суток, касания — по мере готовности суток"
  ALPHA_HOME="$BASE" JOBS="${IMPORT_JOBS:-6}" "$BASE/bin/archive-import.sh" "$HOME_E/root" "${DAYS[@]}" &
  IMP=$!
  n_syms=$(tail -n +2 "$HOME_E/root/instruments.csv" | grep -vc '^#')
  ready_days() {
    for d in "${DAYS[@]}"; do
      [ -f "$HOME_E/study/touches/$d/.done" ] && continue
      f="$HOME_E/root/verify-logs/$d.log"; [ -f "$f" ] || continue
      [ "$(grep -oE '^verify: [A-Z0-9]+' "$f" | sort -u | wc -l)" -ge "$n_syms" ] && echo "$d"
    done
  }
  while kill -0 "$IMP" 2>/dev/null; do
    ready=$(ready_days | tr '\n' ' ')
    if [ -n "${ready// /}" ]; then
      echo "== $(date -u +%FT%TZ) конвейер: касания суток $ready"
      ALPHA_HOME="$HOME_E" NIGHT_TAG="$TAG-h3" TOUCHES_ONLY=1 H3_DAYS="$ready" "$BASE/bin/nightly-grid.sh"
      ALPHA_HOME="$HOME_E" FROM_DAY="$FROM" OOS_DIR="b5/epoch-frozen" RUNS=study/runs-2026-09-19.csv \
        "$BASE/bin/oos-frozen.sh" > /dev/null 2>&1
    else
      sleep 60
    fi
  done
  wait "$IMP"
else
  HOME_E="$BASE"
  FROM="${FROM_DAY:-$(ls "$BASE/root" | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | sort -u | head -1)}"
  TAG=e-collected
fi

echo "== $(date -u +%FT%TZ) эпоха $MODE: ночной набор (тег $TAG)"
ALPHA_HOME="$HOME_E" NIGHT_TAG="$TAG" "$BASE/bin/nightly-grid.sh"
echo "== $(date -u +%FT%TZ) эпоха $MODE: замороженная ветка F10 с $FROM"
ALPHA_HOME="$HOME_E" FROM_DAY="$FROM" OOS_DIR="b5/epoch-frozen" RUNS=study/runs-2026-09-19.csv \
  "$BASE/bin/oos-frozen.sh"
echo "== $(date -u +%FT%TZ) эпоха $MODE: готово"
