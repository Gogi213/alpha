#!/usr/bin/env bash
# TK-493 F8 (П-37 r3.1 §14 п.4): общий запуск торгового счёта и контролей. Запуск - ТОЛЬКО по команде CEO и ТОЛЬКО на Steam Deck (stdlib + бинарь ядра).
#
#   bash f8_run.sh                       показать план (стадии, число единиц, что уже сделано) и выйти; ничего не запускается
#   bash f8_run.sh --go                  проверки, затем счёт всех стадий по порядку; возобновление: единицы с меткой .done пропускаются
#   bash f8_run.sh --go --stage w1,w2    только эти стадии (w1 w2 w3 w4 mkplac pl rp sm n17)
#   bash f8_run.sh --go --only REGEX     только единицы, чей id подходит под REGEX
#   bash f8_run.sh --go --keep-going     не останавливаться на первой ошибке (по умолчанию - остановка)
#   bash f8_run.sh --git-token           (на ПК, в рабочем дереве git) проверить params и записать f8_git_ok для копии на Deck
#   bash f8_run.sh --go --dry-form       сухой прогон на панелях ФОРМИРОВАНИЯ (F8_DIR/F8_CWD задают каталоги формационной пробы); боевые проверки пропущены
#
# Защита от повторного/случайного запуска (боевой режим): (1) без --go ничего не запускается; (2) params закоммичены и не изменены: в git-дереве -
# `git log` по файлу params непуст, файл чист относительно HEAD, коммит - предок HEAD; на Deck (копия без git) - токен f8_git_ok, записанный --git-token
# на ПК, с теми же sha256; sha256 файла params и списка единиц равны записанным в f8_expected.env; (3) sha256 бинаря равен записанному;
# (4) выходные каталоги счёта (~/xdl9/out, $F8_CWD/out, <репозиторий>/data/xdl9/out) пусты или не существуют перед ПЕРВЫМ запуском
# (метка $F8_CWD/.f8_started; при возобновлении сверяются sha256 params, списка и бинаря с записанными в метке); (5) один запуск за раз (flock);
# (6) свободного места на Deck не меньше 2 x F8_NEED_GB; (7) потоков не больше 4 (F8_MAX_THREADS), приоритет nice 19 (на Deck идут чужие аудиторы).
# Единица = одна команда списка f8_units.tsv (id, стадия, команда, glob выходов). Выходы упавшей единицы стираются перед повторной попыткой.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
F8_DIR="${F8_DIR:-$HERE}"
F8_CWD="${F8_CWD:-$HOME/xdl9/sel/waves}"
GO=0; DRY=0; KEEP=0; STAGES=""; ONLY=""; TOKEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --go) GO=1;; --dry-form) DRY=1;; --keep-going) KEEP=1;; --git-token) TOKEN=1;;
    --stage) STAGES="$2"; shift;; --only) ONLY="$2"; shift;;
    *) echo "неизвестный аргумент: $1"; exit 2;;
  esac; shift
done
UNITS="${F8_UNITS:-$F8_DIR/f8_units.tsv}"
[ -f "$F8_DIR/f8_expected.env" ] || { echo "нет $F8_DIR/f8_expected.env"; exit 2; }
# shellcheck disable=SC1090
. <(tr -d '\r' < "$F8_DIR/f8_expected.env")
BIN="${F8_BIN:-$HOME/xdl9/bin/statarb-xdl9-4ef45cc8}"
THREADS="${THREADS:-4}"
[ "$THREADS" -le "${F8_MAX_THREADS:-4}" ] || { echo "THREADS=$THREADS больше $F8_MAX_THREADS (на Deck чужие аудиторы)"; exit 2; }
export THREADS
sha_of() { sha256sum "$1" | cut -d' ' -f1; }
die() { echo "ОТКАЗ: $*"; exit 1; }

# ---------------------------------------------------------------- git-проверки (ПК) и токен (Deck)
REPO=""
if git -C "$HERE" rev-parse --show-toplevel >/dev/null 2>&1; then REPO="$(git -C "$HERE" rev-parse --show-toplevel)"; fi
blob_sha() { git -C "$REPO" show "HEAD:$1" 2>/dev/null | sha256sum | cut -d' ' -f1; }      # sha256 закоммиченного содержимого (не рабочей копии: autocrlf)
check_git() {
  [ -n "$REPO" ] || return 2
  local rel="$F8_PARAMS_REL" urel="${UNITS#$REPO/}" c
  c="$(git -C "$REPO" log -1 --format=%H -- "$rel")"
  [ -n "$c" ] || die "params ($rel) не закоммичены (git log по файлу пуст)"
  [ -z "$(git -C "$REPO" status --porcelain -- "$rel")" ] || die "params ($rel) изменены относительно HEAD"
  git -C "$REPO" merge-base --is-ancestor "$c" HEAD || die "коммит params $c не предок HEAD"
  [ "$(blob_sha "$rel")" = "$F8_PARAMS_SHA256" ] || die "sha256 закоммиченных params не равен записанному в f8_expected.env"
  git -C "$REPO" cat-file -e "HEAD:$urel" 2>/dev/null || die "список единиц ($urel) не в git"
  [ -z "$(git -C "$REPO" status --porcelain -- "$urel")" ] || die "список единиц изменён относительно HEAD"
  [ "$(blob_sha "$urel")" = "$F8_UNITS_SHA256" ] || die "sha256 закоммиченного списка единиц не равен записанному в f8_expected.env"
  PARAMS_COMMIT="$c"; HEAD_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
  return 0
}
if [ "$TOKEN" = 1 ]; then
  check_git || die "--git-token нужен рабочее дерево git"
  { echo "params_commit=$PARAMS_COMMIT"; echo "params_sha256=$F8_PARAMS_SHA256"; echo "units_sha256=$F8_UNITS_SHA256"; echo "head=$HEAD_COMMIT"; } > "$F8_DIR/f8_git_ok"
  echo "токен записан: $F8_DIR/f8_git_ok (params $PARAMS_COMMIT)"; exit 0
fi

# ---------------------------------------------------------------- план
mapfile -t LINES < <(tail -n +2 "$UNITS" | tr -d '\r')
total=${#LINES[@]}; done_n=0
for ln in "${LINES[@]}"; do id="${ln%%$'\t'*}"; [ -e "$F8_CWD/.done/${id//[:\/]/_}" ] && done_n=$((done_n+1)); done
echo "единиц $total, готово $done_n; рабочий каталог $F8_CWD; список $UNITS"
printf '%s\n' "${LINES[@]}" | awk -F'\t' '{c[$2]++} END{for(k in c) printf "  стадия %s: %d\n", k, c[k]}' | sort
if [ "$GO" != 1 ]; then echo "без --go ничего не запускается."; exit 0; fi

# ---------------------------------------------------------------- проверки
if [ "$DRY" != 1 ]; then
  if check_git; then :; else
    tok="$F8_DIR/f8_git_ok"; [ -f "$tok" ] || die "нет git-дерева и нет токена f8_git_ok (на ПК: bash f8_run.sh --git-token)"
    pc="$(grep '^params_commit=' "$tok" | cut -d= -f2)"; ps="$(grep '^params_sha256=' "$tok" | cut -d= -f2)"; us="$(grep '^units_sha256=' "$tok" | cut -d= -f2)"
    [ -n "$pc" ] || die "в токене нет коммита params"
    PARAMS_FILE="${F8_PARAMS_FILE:-$HOME/xdl9/sel/$(basename "$F8_PARAMS_REL")}"
    [ -f "$PARAMS_FILE" ] || die "нет копии params на Deck: $PARAMS_FILE"
    [ "$(sha_of "$PARAMS_FILE")" = "$ps" ] && [ "$ps" = "$F8_PARAMS_SHA256" ] || die "sha256 копии params не равен токену/записанному"
    [ "$(sha_of "$UNITS")" = "$us" ] && [ "$us" = "$F8_UNITS_SHA256" ] || die "sha256 списка единиц не равен токену/записанному"
    PARAMS_COMMIT="$pc"; HEAD_COMMIT="$(grep '^head=' "$tok" | cut -d= -f2)"
  fi
  [ "$(sha_of "$UNITS")" = "$F8_UNITS_SHA256" ] || [ -n "$REPO" ] || die "sha256 списка единиц не равен записанному в f8_expected.env"
  STARTED="$F8_CWD/.f8_started"
  if [ ! -e "$STARTED" ]; then
    for d in "$HOME/xdl9/out" "$F8_CWD/out" ${REPO:+"$REPO/data/xdl9/out"}; do
      if [ -e "$d" ]; then
        { [ -d "$d" ] && [ -z "$(ls -A "$d")" ]; } || die "выходной каталог $d не пуст перед первым запуском (запуск уже был? удалять - только по слову CEO)"
      fi
    done
    for d in d2 d2_pl d2_rp d2_n17 w4; do [ ! -e "$F8_CWD/$d" ] || die "каталог $d уже существует до первого запуска"; done
  else
    . <(tr -d '\r' < "$STARTED")
    [ "${started_params_sha256:-}" = "$F8_PARAMS_SHA256" ] && [ "${started_units_sha256:-}" = "$F8_UNITS_SHA256" ] && [ "${started_bin_sha256:-}" = "$F8_BIN_SHA256" ] \
      || die "метка .f8_started записана при другом params/списке/бинаре - возобновление запрещено"
  fi
fi
[ -x "$BIN" ] || die "нет бинаря $BIN"
[ "$(sha_of "$BIN")" = "$F8_BIN_SHA256" ] || die "sha256 бинаря не равен записанному ($F8_BIN_SHA256)"
mkdir -p "$F8_CWD/.done" "$F8_CWD/.fail" "$F8_CWD/log"
exec 9>"$F8_CWD/.f8_lock"; flock -n 9 || die "уже идёт другой запуск (flock $F8_CWD/.f8_lock)"
avail=$(df -BG --output=avail "$F8_CWD" | tail -1 | tr -dc 0-9)
[ "$avail" -ge $((2 * F8_NEED_GB)) ] || die "свободно ${avail} ГБ, нужно не меньше $((2 * F8_NEED_GB)) ГБ (2 x ${F8_NEED_GB})"
if [ "$DRY" != 1 ] && [ ! -e "$F8_CWD/.f8_started" ]; then
  { echo "started_at=$(date -Is)"; echo "started_params_commit=$PARAMS_COMMIT"; echo "started_params_sha256=$F8_PARAMS_SHA256"; echo "started_units_sha256=$F8_UNITS_SHA256"
    echo "started_bin_sha256=$F8_BIN_SHA256"; echo "started_head=$HEAD_COMMIT"; } > "$F8_CWD/.f8_started"
fi

# ---------------------------------------------------------------- счёт
TJ='tj() { seq -s, 0 $(( $(wc -l < "$1") - 2 )); }'      # номера заданий 0..n-1 (в файле заголовок + n строк)
cd "$F8_CWD" || die "нет $F8_CWD"
mkdir -p w4
fails=0; ran=0
for ln in "${LINES[@]}"; do
  IFS=$'\t' read -r id stage cmd outs <<< "$ln"
  if [ -n "$STAGES" ] && ! [[ ",$STAGES," == *",$stage,"* ]]; then continue; fi
  if [ -n "$ONLY" ] && ! [[ "$id" =~ $ONLY ]]; then continue; fi
  key="${id//[:\/]/_}"
  [ -e ".done/$key" ] && continue
  eval "outs_exp=\"$outs\""
  read -ra OW <<< "$outs_exp"
  mkdir -p "$(dirname "${OW[0]}")"
  # стереть недописанные выходы прежней попытки (только у единицы без метки .done); несовпавший glob остаётся буквальной строкой и не существует
  for w in "${OW[@]}"; do
    # shellcheck disable=SC2086
    for f in $w; do
      [ -e "$f" ] || continue
      rp="$(realpath -- "$f")"
      case "$rp" in "$HOME"/xdl9/plac/*|"$HOME"/xdl9/plac_form/*|"$HOME"/xdl9/sel/*) ;; *) die "стирание вне рабочих каталогов отклонено: $rp";; esac
      rm -rf -- "$f"
    done
  done
  rm -f ".fail/$key"
  t0=$(date +%s); echo "[$(date -Is)] $id: старт"
  nice -n 19 bash -c "set -u; $TJ; THREADS=$THREADS; set -o pipefail; $cmd" > "log/$key.log" 2>&1
  rc=$?; t1=$(date +%s)
  if [ "$rc" = 0 ]; then
    echo "rc=0 secs=$((t1-t0)) at=$(date -Is)" > ".done/$key"; ran=$((ran+1)); echo "[$(date -Is)] $id: готово за $((t1-t0)) с"
  else
    echo "rc=$rc secs=$((t1-t0)) at=$(date -Is)" > ".fail/$key"; fails=$((fails+1)); echo "[$(date -Is)] $id: ОШИБКА rc=$rc (log/$key.log)"
    [ "$KEEP" = 1 ] || { echo "остановка на первой ошибке (--keep-going не задан)"; exit 1; }
  fi
done
echo "запущено $ran, ошибок $fails"
[ "$fails" = 0 ] || exit 1
left=0; for ln in "${LINES[@]}"; do id="${ln%%$'\t'*}"; [ -e ".done/${id//[:\/]/_}" ] || left=$((left+1)); done
[ "$left" != 0 ] || { date -Is > .f8_finished; echo "все единицы готовы"; }
