#!/usr/bin/env bash
# Эпоха «история» (владелец 22.09): сутки публичного архива Bybit → корень эпохи в формате нашей записи.
#   archive-import.sh <корень эпохи> <сутки…>        символы — из <корень>/instruments.csv
# На каждую монету-сутки: скачать `ob200.data.zip` (quote-saver.bycsi.com) и сделки (public.bybit.com),
# распаковать потоком в `alpha lob import-archive`, удалить сырьё. Итог суток — строка сверки в
# <корень>/verify-logs/<сутки>.log в формате нашей ночной сверки (`verify: <SYM> status=ok|fail …`):
# day_root() ночи строит из неё маркеры K1 суток. status=fail — разрывы номеров `u` в потоке, сутки
# без снимка или ошибка импорта. Монеты, которых в архиве нет (листинг позже), — строка `missing`.
# Параллельно JOBS монет (умолчание 4: загрузка — сеть, импорт — одно ядро на монету).
set -uo pipefail
ROOT="${1:?корень эпохи}"; shift
DAYS=("$@"); [ ${#DAYS[@]} -gt 0 ] || { echo "нужны сутки"; exit 1; }
ALPHA_HOME="${ALPHA_HOME:-$HOME/alpha}"
BIN="${BIN:-$ALPHA_HOME/bin/alpha}"
JOBS="${JOBS:-4}"
TMP="${TMP_DIR:-$ROOT/.tmp}"
mkdir -p "$ROOT/verify-logs" "$TMP"
[ -f "$ROOT/instruments.csv" ] || { echo "нет $ROOT/instruments.csv"; exit 1; }
SYMS=$(tail -n +2 "$ROOT/instruments.csv" | cut -d, -f1 | grep -v '^#')

one() {
  local sym=$1 day=$2
  local z="$TMP/$sym-$day.zip" t="$TMP/$sym-$day.csv"
  local out="$ROOT/$sym-$day.binlog"
  [ -f "$out" ] && { echo "verify: $sym status=ok source=archive (уже импортирован)"; return; }
  if ! curl -sf --retry 3 --max-time 900 -o "$z" \
      "https://quote-saver.bycsi.com/orderbook/linear/$sym/${day}_${sym}_ob200.data.zip"; then
    rm -f "$z"; echo "verify: $sym status=missing source=archive (нет стакана в архиве)"; return
  fi
  if ! curl -sf --retry 3 --max-time 600 "https://public.bybit.com/trading/$sym/$sym$day.csv.gz" | gunzip -c > "$t"; then
    rm -f "$z" "$t"; echo "verify: $sym status=missing source=archive (нет сделок в архиве)"; return
  fi
  local res
  if res=$(python3 -c "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); sys.stdout.buffer.write(z.read(z.namelist()[0]))" "$z" \
        | nice -n 10 "$BIN" lob import-archive --symbol "$sym" --day "$day" --ob - --trades "$t" \
            --instruments "$ROOT/instruments.csv" --root "$ROOT" 2>&1); then
    local gaps; gaps=$(echo "$res" | grep -o "разрывов u [0-9]*" | grep -o "[0-9]*$")
    if [ "${gaps:-1}" = "0" ]; then st=ok; else st=fail; fi
    echo "verify: $sym status=$st source=archive $(echo "$res" | tail -1 | sed 's/^import-archive: [^ ]* — //')"
  else
    rm -f "$out" "$out.part"
    echo "verify: $sym status=fail source=archive ошибка импорта: $(echo "$res" | tail -1 | cut -c1-200)"
  fi
  rm -f "$z" "$t"
}
export -f one
export ROOT TMP BIN

for day in "${DAYS[@]}"; do
  log="$ROOT/verify-logs/$day.log"
  echo "== $(date -u +%FT%TZ) импорт $day: $(echo "$SYMS" | wc -l) монет, $JOBS параллельно"
  echo "$SYMS" | xargs -r -P "$JOBS" -I{} bash -c 'one "$@"' _ {} "$day" >> "$log" 2>&1
  echo "== $(date -u +%FT%TZ) $day: ok $(grep -c 'status=ok' "$log"), fail $(grep -c 'status=fail' "$log"), нет в архиве $(grep -c 'status=missing' "$log"); занято $(du -sh "$ROOT" | cut -f1)"
done
# Маркеры корня (K1 при прогоне по корню целиком): ok, если монета импортирована хоть за одни сутки;
# по суткам честнее — day_root() ночи берёт маркеры из verify-logs/<сутки>.log.
for s in $SYMS; do
  if grep -qh "^verify: $s status=ok" "$ROOT"/verify-logs/*.log 2>/dev/null; then echo ok > "$ROOT/verify-$s.status"
  else echo fail > "$ROOT/verify-$s.status"; fi
done
rmdir "$TMP" 2>/dev/null || true
