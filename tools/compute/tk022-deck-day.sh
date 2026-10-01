#!/usr/bin/env bash
# TK-022 (В-161): сутки D — три прохода (основной, t9, Г-86) с ЛОКАЛЬНОЙ копии суток; запуск на Steam Deck.
#   tk022-deck-day.sh <YYYY-MM-DD> [дом]   дом по умолчанию — ~/alpha/epochs/e-<мес> (воротам — отдельный дом с пустым b5)
#   PREP=1 …    только собрать вид и три отрезка seg1/seg2/seg3.sh (основной / t9 / Г-86), без счёта
#   PASS=n …    выполнить отрезок n готового вида (для очереди alpha-gridq: один процесс = один проход); вид собирает PREP
#   STAGE=<каталог> — вместо /dev/shm/alpha-stage (В-162: stage в RAM деки; сухие проверки)
# Вход (Инженер, TK-026): $STAGE/<D>/{root/<SYM>-<D>.binlog (mtime с ящика), D20/ (= D20/<D>/ с ящика), .ready};
# довесок переноса через полночь читает D+1 → нужен $STAGE/<D+1>/.ready, если D+1 есть в e-<мес>/root (как у прежних результатов).
# Выход: b5/<клетка>/<D>/ (как у jall-скриптов), статус ~/alpha/tk022/status/<D>.rc (только в своём доме) и <D>.p<n>.rc
# по проходам (rc= sec=), .release на сутки (см. rel). Сайдкары .binlog.events не берутся: первый проход считает и кладёт
# их рядом с видом, остальные проходы читают готовые (маркер <D>.p1.grid-done — проход 1 закончил счёт событий и сетку).
set -uo pipefail
D="${1:?сутки YYYY-MM-DD}"
A="$HOME/alpha"
MN=(jan feb mar apr may jun)
M="${MN[$((10#${D:5:2} - 1))]}"
EH="$A/epochs/e-$M"
H="${2:-$EH}"
N="$(date -u -d "$D +1 day" +%F)"
ST="${STAGE:-/dev/shm/alpha-stage}"
S="$ST/$D"; SN="$ST/$N"
SC="$A/tmp-p07/cells-by-day/jall-$M-$D.sh"
V="$A/tk022/view/$D"
ST_DIR="$A/tk022/status"

build_view() {
  [ -e "$S/.ready" ] || { echo "нет $S/.ready" >&2; exit 4; }
  [ -e "$SC" ] || { echo "нет сценария $SC" >&2; exit 4; }
  local need_next=0
  compgen -G "$EH/root/*-$N.binlog" >/dev/null && need_next=1
  if [ $need_next = 1 ] && [ ! -e "$SN/.ready" ]; then echo "нет $SN/.ready (довесок D+1)" >&2; exit 4; fi
  rm -rf "$V"; mkdir -p "$V/root-$D" "$V/carry" "$V/D20" "$ST_DIR"
  local f b
  # корень суток: всё не-бинлоговое как в study/root-<D>; бинлоги — те же имена, цель — копия суток
  for f in "$EH/study/root-$D"/*; do
    b="$(basename "$f")"
    case "$b" in
      *.binlog) [ -e "$S/root/$b" ] || { echo "в stage нет $b" >&2; exit 5; }; ln -s "$S/root/$b" "$V/root-$D/$b" ;;
      *.events) ;;
      *) ln -s "$(readlink -f "$f")" "$V/root-$D/$b" ;;
    esac
  done
  # корень довеска: как e-<мес>/root, но бинлоги D+1 — на копию, прочих суток нет (читается только D+1)
  for f in "$EH/root"/*; do
    b="$(basename "$f")"
    case "$b" in
      *-"$N".binlog) ln -s "$SN/root/$b" "$V/carry/$b" ;;
      *.binlog|*.events) ;;
      *) ln -s "$(readlink -f "$f")" "$V/carry/$b" ;;
    esac
  done
  ln -s "$S/D20" "$V/D20/$D"
  sed -E "s# --root study/root-$D # --root $V/root-$D #g; s# --touches-from study/approaches/D20 # --touches-from $V/D20 #g; s# --carry-root root # --carry-root $V/carry #g" "$SC" > "$V/run.sh"
  local n
  n="$(grep -o -F -e "--root $V/root-$D " -e "--touches-from $V/D20 " -e "--carry-root $V/carry " "$V/run.sh" | wc -l)"
  [ "$n" = 9 ] || { echo "подмена путей: $n вместо 9" >&2; exit 6; }
  # три отрезка по строкам «set -e»: 1 основной (+ разбор по клеткам), 2 t9, 3 Г-86 (свой каталог сетки: не делит .cellstmp с отрезком 1)
  awk -v v="$V" 'BEGIN { k = 0 } /^set -e$/ { k++ } { print > (v "/seg" k ".sh") }' "$V/run.sh"
  [ -e "$V/seg3.sh" ] && [ ! -e "$V/seg4.sh" ] || { echo "отрезков не три" >&2; exit 6; }
  sed -i -E "s#\.cellstmp-$D#.g86tmp-$D#g" "$V/seg3.sh"
  sed -i -E "/^cp b5\/\.cellstmp-$D\.log /a touch \"$ST_DIR/$D.p1.grid-done\"" "$V/seg1.sh"
  grep -q "p1.grid-done" "$V/seg1.sh" || { echo "в отрезке 1 нет строки cp лога сетки" >&2; exit 6; }
  rm -f "$ST_DIR/$D.p1.grid-done"
}

prep_home() {
  mkdir -p "$H/b5" "$ST_DIR"
  [ -e "$H/bin" ] || ln -s "$A/bin" "$H/bin"
  # дом ворот (не e-<мес>): относительные --regime-from/--sigma-from ведут на study/ эпохи
  if [ "$H" != "$EH" ]; then
    mkdir -p "$H/study"
    local x
    for x in $(grep -ohE 'study/[A-Za-z0-9_-]+' "$SC" | sort -u); do
      case "$x" in study/root-*|study/approaches) ;; *) [ -e "$H/$x" ] || ln -s "$EH/$x" "$H/$x" ;; esac
    done
  fi
}

# .release: сутки X свободны, когда готов их счёт и счёт X−1 (X−1 брал X довеском); первые сутки — без X−1
ok() { grep -q '^rc=0 ' "$ST_DIR/$1.rc" 2>/dev/null; }
rel() {
  local x="$1" p; p="$(date -u -d "$x -1 day" +%F)"
  if ok "$x" && { [ "$x" = 2026-01-01 ] || ok "$p"; }; then touch "$ST/$x/.release" 2>/dev/null || true; fi
}
finish() {
  [ "$H" = "$EH" ] || return 0
  local i s=0 r
  for i in 1 2 3; do
    grep -q '^rc=0 ' "$ST_DIR/$D.p$i.rc" 2>/dev/null || return 0
    r="$(sed -n 's/.* sec=\([0-9]*\).*/\1/p' "$ST_DIR/$D.p$i.rc")"; s=$((s + ${r:-0}))
  done
  mkdir "$ST_DIR/$D.fin" 2>/dev/null || return 0
  echo "rc=0 sec=$s" > "$ST_DIR/$D.rc"
  rel "$D"; rel "$N"
  rm -rf "$V"
}

if [ -n "${PREP:-}" ]; then
  build_view; echo "вид $V собран: подмена путей 9/9, отрезки seg1..3"; exit 0
fi

if [ -n "${PASS:-}" ]; then
  [ -e "$V/seg$PASS.sh" ] || { echo "нет $V/seg$PASS.sh (сначала PREP=1)" >&2; exit 4; }
  prep_home
  t0=$(date +%s)
  ( cd "$H" && bash "$V/seg$PASS.sh" ); rc=$?
  sec=$(( $(date +%s) - t0 ))
  echo "rc=$rc sec=$sec"
  [ "$H" = "$EH" ] || exit $rc
  echo "rc=$rc sec=$sec t0=$t0" > "$ST_DIR/$D.p$PASS.rc"
  [ $rc = 0 ] || exit $rc
  finish
  exit 0
fi

build_view
prep_home
t0=$(date +%s)
( cd "$H" && bash "$V/run.sh" ) ; rc=$?
sec=$(( $(date +%s) - t0 ))
echo "rc=$rc sec=$sec"
[ "$H" = "$EH" ] || exit $rc
echo "rc=$rc sec=$sec" > "$ST_DIR/$D.rc"
[ $rc = 0 ] || exit $rc
rel "$D"; rel "$N"
rm -rf "$V"
exit 0
