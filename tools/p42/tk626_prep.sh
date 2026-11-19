#!/usr/bin/env bash
# TK-626 / П-42 §13.28.1 п. 5: подготовка данных точки вперёд (вселенная U_k = B ∪ N_k, деревья окон, кэш `breakout convert`, манифест). Без P&L, движок не запускается.
# Запуск на srv под systemd-run (Nice=19, idle IO): systemd-run --unit=tk626-prep-<точка> -p Nice=19 -p IOSchedulingClass=idle --collect bash tk626_prep.sh <L1…L5|test>
# Точка читается после конца суток D (UTC): подготовка — после D + 1, перед чтением (§13.28.1 п. 8.4). `test` — проверка скрипта на готовых данных до заморозки (п. 5 последний пункт):
#   окно w1, D = 2026-11-16, каталог $R/prep-test, кэш не строится (NOCONVERT=1 по умолчанию), движок не запускается.
# Готовность — файл $R/<точка>/READY (код возврата); журнал — $R/<точка>/log.
set -uo pipefail
MODE="${1:?L1…L5|test}"
R=${R:-/data/p42-tk616/fwd}
CODE=${CODE:-/data/p42-tk626/code/tools/p42}
FREEZE=${FREEZE:-/data/p42-tk626/code/docs/research/freeze/tk626-frozen.json}
BASE=${BASE:-/data/p42-tk621/universe}
BIN=${BIN:-/data/p42-tk616/alpha-mk.396989e9}                       # замороженный бинарь (md5 396989e9…)
if [ "$MODE" = test ]; then
  PID=prep-test; D=2026-11-16; WIN=w1; TEST="--allow-test"; WINARG="--windows w1"; NOCONVERT=${NOCONVERT:-1}
else
  PID=$MODE; TEST=""; WIN=""; WINARG=""; NOCONVERT=${NOCONVERT:-0}
  D=$(python3 -c "import json,sys; p=[x for x in json.load(open('$FREEZE'))['points'] if x['id']=='$PID']; print(p[0]['d'] if p else '')")
  [ -n "$D" ] || { echo "точка $PID не из заморозки" >&2; exit 2; }
  WIN=$(python3 -c "import json; print(','.join([x for x in json.load(open('$FREEZE'))['points'] if x['id']=='$PID'][0]['windows']))")
  # точка читается только после конца суток D (UTC): подготовка раньше не имеет смысла (данных D нет)
  [ "$(date -u +%F)" \> "$D" ] || { echo "дата точки $D ещё не закончилась (UTC $(date -u +%F))" >&2; exit 2; }
fi
P=$R/$PID
[ ! -e "$P" ] || { echo "каталог $P уже есть: подготовка не повторяется поверх (удалить — решение исполнителя до чтения точки)" >&2; exit 2; }
mkdir -p "$P/list" "$R/dl" "$R/rest"; : > "$P/log"
fail() { echo "FAIL $*" | tee -a "$P/log"; echo 1 > "$P/READY"; exit 1; }
say() { echo "$(date -u +%T) $*" >> "$P/log"; }
PREV=""
if [ "$MODE" != test ]; then
  PV=$(python3 -c "import json; ids=[x['id'] for x in json.load(open('$FREEZE'))['points']]; i=ids.index('$PID'); print(ids[i-1] if i else '')")
  [ -z "$PV" ] || { [ -d "$R/$PV/universe" ] || fail "нет вселенной предыдущей точки $PV ($R/$PV/universe)"; PREV="--prev $R/$PV/universe"; }
fi
say "листинг S3 01.07…$D"
python3 "$CODE/tk621_list.py" "$P/list" 2026-07-01 "$D" >> "$P/log" 2>&1 || fail list
say "вселенная"
python3 "$CODE/tk626_prep.py" universe --list-dir "$P/list" --base-dir "$BASE" --d "$D" --out "$P/universe" $PREV >> "$P/log" 2>&1 || fail universe
say "скачивание (общий каталог $R/dl, возобновляемо)"
rm -f "$R/dl/log/miss.tsv"                                            # журнал неудач — только этого прогона (скачанное ранее пропускается по .sha256)
python3 "$CODE/tk621_dl.py" "$P/list" "$R/dl" 16 >> "$P/log" 2>&1 || fail download
[ ! -s "$R/dl/log/miss.tsv" ] || say "НЕ СКАЧАНО (строк $(wc -l < "$R/dl/log/miss.tsv")): см. $R/dl/log/miss.tsv"
say "REST-добор провалов минуток"
python3 "$CODE/tk621_restfill.py" "$P/list" "$R/rest" >> "$P/log" 2>&1 || fail restfill
cp "$R/rest/log/restfill.tsv" "$P/restfill.tsv" 2>/dev/null || : > "$P/restfill.tsv"
say "деревья окон $WIN"
python3 "$CODE/tk626_prep.py" tree --out "$P" --universe "$P/universe" --d "$D" $WINARG --dl "$R/dl" --rest "$R/rest" --freeze "$FREEZE" $TEST >> "$P/log" 2>&1 || fail tree
python3 "$CODE/tk626_prep.py" check --out "$P" --universe "$P/universe" --d "$D" $WINARG --freeze "$FREEZE" >> "$P/log" 2>&1 || fail check
if [ "$NOCONVERT" != 1 ]; then
  for w in ${WIN//,/ }; do
    T0=$(python3 -c "import json; print([x for x in json.load(open('$FREEZE'))['windows'] if x['name']=='$w'][0]['t0'])")
    say "convert $w P42_T0=$T0"
    P42_T0=$T0 "$BIN" breakout convert --src "$P/$w/klines" --out "$P/$w/cache" --threads 4 >> "$P/log" 2>&1 || fail "convert $w"
    (cd "$P/$w/cache" && find . -type f -name '*.b42' | LC_ALL=C sort | xargs -r sha256sum) > "$P/$w/manifest-cache-sha256.txt"
  done
fi
{ echo "точка $PID D $D окна $WIN"
  for f in symbols-all symbols-crypto symbols-tradfi; do echo "universe/$f.txt $(tr ',' '\n' < "$P/universe/$f.txt" | grep -c .) монет, sha256 $(sha256sum "$P/universe/$f.txt" | cut -d' ' -f1)"; done
  echo "universe/contracts.csv sha256 $(sha256sum "$P/universe/contracts.csv" | cut -d' ' -f1)"
  echo "binary $(sha256sum "$BIN" | cut -d' ' -f1) md5 $(md5sum "$BIN" | cut -d' ' -f1)"; echo "freeze sha256 $(sha256sum "$FREEZE" | cut -d' ' -f1)"
  for w in ${WIN//,/ }; do echo "nofile-$w.tsv $(wc -l < "$P/nofile-$w.tsv") строк"; done
  echo "restfill.tsv $(wc -l < "$P/restfill.tsv") строк; список провалов (gaps.tsv) $(wc -l < "$P/list/gaps.tsv") строк"; } | tee "$P/MANIFEST" >> "$P/log"
echo 0 > "$P/READY"
say "READY"
