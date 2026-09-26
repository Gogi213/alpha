#!/usr/bin/env bash
# Архив Bybit → Hetzner Storage Box (T-30, владелец 27.09, В-118): 01.01–31.07.2026 и дальше новые сутки по мере
# появления; все месяцы в одном месте — и 01.08–15.09, хотя они есть на Steam Deck (`e-aug`/`e-archive`; CEO 27.09). Тот же импорт, что
# `archive-import.sh`, конвейером на VPS, без ручных шагов:
#   рабочие (JOBS): монета-сутки скачана → импорт в бинлог → в накопитель $BASE/stage (сырьё удалено сразу);
#   заливщик: накопилось ≥ BATCH_GB (или импорт кончился) → пачка одной передачей rsync → манифест sha256,
#   сверка сумм самим Storage Box → пачка удалена с VPS → её строки в журнал готового.
# Владелец 27.09: «балком заливать по 20 гб», CEO 27.09 — 10 ГБ под диск VPS (на мелких файлах время съедали рукопожатия и сверка по файлу);
# «чтобы данные не сидели на передержке» — временное: только $BASE/{tmp,stage,batch-*}; рабочие ждут, пока на /
# меньше MIN_FREE_GB, поэтому накопитель + заливаемая пачка ≤ (свободно − MIN_FREE_GB).
# Храним только бинлог, как `e-aug`: сырьё Bybit (ob200 zip + сделки ≈ 1,65× бинлога) скачивается заново.
#
#   archive-to-box.sh <первые сутки> [<последние сутки>|yesterday]
# Служба: tools/alpha-archive-box.{service,timer} — таймер раз в сутки догоняет новые сутки (готовые пропускаются).
# Журнал готового — $BASE/log/done.txt: «<сутки> <монета> ok|gaps|missing|fail <байт> <sha256> <пачка>» (строка —
# только после сверки пачки на Storage Box). ok/gaps/missing не повторяются; fail — до 3 попыток за запуски; missing
# за последние 3 суток не пишется (архив Bybit выкладывает сутки с задержкой) — попробуется завтра.
# Раскладка на Storage Box — как на Steam Deck: alpha/epochs/e-<мес>/root/<SYM>-<сутки>.binlog, instruments.csv,
# verify-logs/<сутки>.log (формат ночной сверки, как archive-import.sh), verify-<SYM>.status; манифесты пачек —
# alpha/epochs/manifests/<пачка>.sha256. Итог — $BASE/log/summary.txt.
set -uo pipefail
FIRST="${1:?первые сутки}"; LAST="${2:-yesterday}"
[ "$LAST" = yesterday ] && LAST=$(date -u -d yesterday +%F)
SKIP_FROM="${SKIP_FROM:-}"; SKIP_TO="${SKIP_TO:-}"   # пропуск диапазона суток (пусто — без пропуска)
BASE="${BASE:-/opt/alpha-archive}"
BIN="${BIN:-$BASE/bin/alpha}"
JOBS="${JOBS:-4}"
BATCH_GB="${BATCH_GB:-10}"      # CEO 27.09: 10 ГБ (было 20 — VPS 73 ГБ, свободно ~30)
STAGE_MAX_GB="${STAGE_MAX_GB:-2}"   # пока пачка заливается, накопитель не больше этого (≈ сутки)
MIN_FREE_GB="${MIN_FREE_GB:-8}"
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
TMP=$BASE/tmp; STAGE=$BASE/stage; LOGD=$BASE/log; DONE=$LOGD/done.txt; STAGED=$LOGD/staged.txt; RUN=$LOGD/run.log
RECENT=$(date -u -d "3 days ago" +%F)
mkdir -p "$TMP" "$STAGE" "$LOGD/verify"; touch "$DONE" "$STAGED"
rm -rf "${TMP:?}"/*   # хвосты прошлого сбоя; накопитель и недолитые пачки остаются — их зальёт этот запуск
log() { echo "$(date -u +%FT%TZ) $*" >> "$RUN"; }
export BIN SBH SSHC TMP STAGE LOGD DONE STAGED RUN MIN_FREE_GB STAGE_MAX_GB BASE RECENT
export -f log

mon() { case ${1:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;;
        07) echo jul;; 08) echo aug;; 09) echo sep;; 10) echo oct;; 11) echo nov;; 12) echo dec;; esac; }
export -f mon

one() {
  local sym=$1 day=$2 m; m=e-$(mon "$day")
  grep -qE "^$day $sym (ok|gaps|missing) " "$DONE" && return
  [ -f "$STAGE/$m/$sym-$day.binlog" ] && return                         # импортирован, ждёт пачки
  ls "$BASE"/batch-*/"$m/root/$sym-$day.binlog" >/dev/null 2>&1 && return      # в недолитой пачке
  [ "$(grep -cE "^$day $sym fail " "$DONE")" -ge 3 ] && return
  # одна пачка на диске: пока она заливается и сверяется, накопитель растёт не больше STAGE_MAX_GB
  while [ "$(df --output=avail -BG / | tail -1 | tr -dc 0-9)" -lt "$MIN_FREE_GB" ] \
     || { ls -d "$BASE"/batch-*/ >/dev/null 2>&1 && [ "$(du -s -BG "$STAGE" | tr -dc 0-9)" -ge "$STAGE_MAX_GB" ]; }; do
    sleep 30
  done
  local w="$TMP/$sym-$day" st note
  mkdir -p "$w"; ln -sf "$BASE/instruments.csv" "$w/instruments.csv"
  if ! curl -sf --retry 5 --retry-delay 10 --max-time 1800 -o "$w/ob.zip" \
      "https://quote-saver.bycsi.com/orderbook/linear/$sym/${day}_${sym}_ob200.data.zip"; then
    st=missing; note="source=archive (нет стакана в архиве)"
  elif ! curl -sf --retry 5 --retry-delay 10 --max-time 1800 "https://public.bybit.com/trading/$sym/$sym$day.csv.gz" \
      | gunzip -c > "$w/trades.csv"; then
    st=missing; note="source=archive (нет сделок в архиве)"
  else
    local res out="$w/$sym-$day.binlog"
    # zip читается потоком (copyfileobj), не целиком в память: стакан тяжёлой монеты распакован — гигабайты
    if res=$(python3 -c "import sys,zipfile,shutil; z=zipfile.ZipFile(sys.argv[1]); shutil.copyfileobj(z.open(z.namelist()[0]), sys.stdout.buffer, 1<<20)" "$w/ob.zip" \
          | nice -n 19 "$BIN" lob import-archive --symbol "$sym" --day "$day" --ob - --trades "$w/trades.csv" \
              --instruments "$w/instruments.csv" --root "$w" 2>&1); then
      local gaps; gaps=$(echo "$res" | grep -o "разрывов u [0-9]*" | grep -o "[0-9]*$")
      if [ "${gaps:-1}" = "0" ]; then st=ok; else st=gaps; fi
      note="source=archive $(echo "$res" | tail -1 | sed 's/^import-archive: [^ ]* — //')"
    else
      st=fail; note="source=archive ошибка импорта: $(echo "$res" | tail -1 | cut -c1-200)"
    fi
  fi
  # сверка — формат ночи: разрывы `u` — status=fail (как archive-import.sh); в журнале — gaps (итог окончательный)
  if [ $st = ok ] || [ $st = gaps ]; then
    mkdir -p "$STAGE/$m"
    flock "$STAGED" sh -c "echo '$day $sym $st' >> '$STAGED'"   # до mv: заливщик видит файл — строка уже есть
    mv "$w/$sym-$day.binlog" "$STAGE/$m/"
  elif [ $st = fail ] || [[ $day < $RECENT ]]; then
    flock "$DONE" sh -c "echo '$day $sym $st 0 - -' >> '$DONE'"
  fi
  mkdir -p "$LOGD/verify/$m"
  [ $st = missing ] && [[ ! $day < $RECENT ]] || \
    echo "verify: $sym status=$([ $st = gaps ] && echo fail || echo $st) $note" >> "$LOGD/verify/$m/$day.log"
  rm -rf "$w"
}
export -f one

# Одна пачка: rsync одной передачей → манифест → суммы на Storage Box (sha256sum по 150 файлов за вызов) → журнал.
push_batch() {
  local b=$1 name; name=$(basename "$b")
  local files; files=$(cd "$b" && find . -name '*.binlog' -printf '%P\n' | sort)
  [ -z "$files" ] && { rm -rf "$b"; return 0; }
  local bytes; bytes=$(du -sb "$b" | cut -f1)
  [ -f "$b.sha256" ] || (cd "$b" && echo "$files" | xargs sha256sum) > "$b.sha256"
  local try t0; t0=$(date +%s)
  for try in 1 2 3; do
    # манифест с путями от домашнего каталога ящика: сверка — один вызов `sha256sum -c` самим ящиком
    sed 's#  #  alpha/epochs/#' "$b.sha256" > "$b.check"
    if rsync -a --partial -e "$SSHC" "$b/" "$SBH:alpha/epochs/"; then
      $SSHC $SBH mkdir -p alpha/epochs/manifests
      rsync -a -e "$SSHC" "$b.check" "$SBH:alpha/epochs/manifests/$name.sha256"
      if $SSHC $SBH sha256sum -c --quiet "alpha/epochs/manifests/$name.sha256" >> "$RUN" 2>&1; then
        while read -r sha f; do
          local fn=${f##*/}; local sym=${fn%-20*}; local day=${fn#"$sym"-}; day=${day%.binlog}
          local st; st=$(grep -E "^$day $sym (ok|gaps)$" "$STAGED" | tail -1 | cut -d' ' -f3)
          echo "$day $sym ${st:-ok} $(stat -c %s "$b/$f") $sha $name"
        done < "$b.sha256" | flock "$DONE" sh -c "cat >> '$DONE'"
        log "пачка $name: $(echo "$files" | wc -l) файлов, $((bytes / 1000000)) МБ за $(( $(date +%s) - t0 )) с, суммы совпали"
        rm -rf "$b" "$b.sha256" "$b.check"
        return 0
      fi
      log "пачка $name: суммы не совпали (попытка $try)"
    else
      log "пачка $name: rsync упал (попытка $try)"
    fi
    sleep 300
  done
  return 1
}

cut_batch() {   # накопитель → пачка (mv в той же ФС — мгновенно)
  local b; b=$BASE/batch-$(date -u +%Y%m%dT%H%M%S)
  mkdir -p "$b"
  local m; for m in "$STAGE"/e-*; do [ -d "$m" ] || continue; mkdir -p "$b/$(basename "$m")/root"
    find "$m" -maxdepth 1 -name '*.binlog' -exec mv -t "$b/$(basename "$m")/root/" {} +; done
  echo "$b"
}
# в накопителе файлы лежат по месяцам плоско (stage/e-jan/X.binlog), в пачке — как на ящике (e-jan/root/X.binlog)

sync_meta() {   # instruments, сверка по суткам, маркеры K1 по месяцам — мелкое, одним rsync
  local meta=$TMP/meta; rm -rf "$meta"; mkdir -p "$meta"
  local m; for m in $(ls "$LOGD/verify"); do
    mkdir -p "$meta/$m/root/verify-logs"; cp "$BASE/instruments.csv" "$meta/$m/root/"
    cp "$LOGD/verify/$m/"*.log "$meta/$m/root/verify-logs/" 2>/dev/null
    local okset; okset=$(while read -r dd s st _; do [ "$st" = ok ] && [ "e-$(mon "$dd")" = "$m" ] && echo "$s"; done < "$DONE" | sort -u)
    for s in $SYMS; do
      if grep -qx "$s" <<< "$okset"; then echo ok; else echo fail; fi > "$meta/$m/root/verify-$s.status"
    done
  done
  rsync -a -e "$SSHC" "$meta/" "$SBH:alpha/epochs/" && rm -rf "$meta"
}

days=(); d=$FIRST
while [[ ! $d > $LAST ]]; do
  [[ -z $SKIP_FROM || $d < $SKIP_FROM || $d > $SKIP_TO ]] && days+=("$d")
  d=$(date -u -d "$d + 1 day" +%F)
done
SYMS=$(tail -n +2 "$BASE/instruments.csv" | cut -d, -f1 | grep -v '^#')
log "== старт ${#days[@]} суток ($FIRST … $LAST, без ${SKIP_FROM:-—} … ${SKIP_TO:-—}) × $(echo "$SYMS" | wc -l) монет, $JOBS параллельно, пачка $BATCH_GB ГБ"

for b in "$BASE"/batch-*/; do [ -d "$b" ] && { push_batch "${b%/}" || { log "недолитая пачка $b не заливается — стоп"; exit 1; }; }; done

for day in "${days[@]}"; do for s in $SYMS; do echo "$s $day"; done; done \
  | xargs -r -P "$JOBS" -n 2 bash -c 'one "$@"' _ &
workers=$!

limit=$((BATCH_GB * 1000000000))
while :; do
  alive=1; kill -0 $workers 2>/dev/null || alive=0
  staged=$(du -sb "$STAGE" | cut -f1)
  if [ "$staged" -ge "$limit" ] || { [ $alive = 0 ] && find "$STAGE" -name '*.binlog' | grep -q .; }; then
    push_batch "$(cut_batch)" || { log "пачка не залилась за 3 попытки — стоп (перезапуск службы продолжит)"; kill $workers; exit 1; }
    sync_meta
    continue
  fi
  [ $alive = 0 ] && break
  sleep 20
done
wait $workers
sync_meta

# итог по суткам — по последней записи журнала на (сутки, монета)
awk '{ st[$1" "$2]=$3 } END { for (k in st) { split(k, a, " "); n[a[1]" "st[k]]++; d[a[1]]=1 }
  for (x in d) printf "%s ok %d gaps %d missing %d fail %d\n", x, n[x" ok"], n[x" gaps"], n[x" missing"], n[x" fail"] }' \
  "$DONE" | sort > "$LOGD/summary.txt"
{ echo "итого: $(awk '{ st[$1" "$2]=$3 } END { for (k in st) n[st[k]]++; for (x in n) printf "%s %d  ", x, n[x] }' "$DONE")"
  echo "на Storage Box: $(awk '$3=="ok"||$3=="gaps" {s+=$4} END {printf "%.1f ГБ", s/1e9}' "$DONE")"
  echo "ошибки импорта (fail): $(awk '$3=="fail" {print $1" "$2}' "$DONE" | sort -u | tr '\n' ';')"; } >> "$LOGD/summary.txt"
log "== готово: $(tail -3 "$LOGD/summary.txt" | head -2 | tr '\n' ' ')"
