#!/usr/bin/env bash
# TK-022 (В-161): сутки D — три прохода (основной, t9, Г-86) с ЛОКАЛЬНОЙ копии суток; запуск на Steam Deck.
#   tk022-deck-day.sh <YYYY-MM-DD> [дом]      дом по умолчанию — ~/alpha/epochs/e-<мес> (воротам — отдельный дом с пустым b5)
# Вход (Инженер, TK-026): ~/alpha/stage/<D>/{root/<SYM>-<D>.binlog (cp -p), D20/ (= D20/<D>/ с ящика), .ready};
# довесок переноса через полночь читает D+1 → нужен ~/alpha/stage/<D+1>/.ready, если D+1 есть в e-<мес>/root (как у прежних результатов).
# Выход: b5/<клетка>/<D>/ (как у jall-скриптов), статус ~/alpha/tk022/status/<D>.rc (только в своём доме), .release на сутки (см. rel).
set -uo pipefail
D="${1:?сутки YYYY-MM-DD}"
A="$HOME/alpha"
MN=(jan feb mar apr may jun)
M="${MN[$((10#${D:5:2} - 1))]}"
EH="$A/epochs/e-$M"
H="${2:-$EH}"
N="$(date -u -d "$D +1 day" +%F)"
ST="${STAGE:-$A/stage}"
S="$ST/$D"; SN="$ST/$N"
SC="$A/tmp-p07/cells-by-day/jall-$M-$D.sh"
[ -e "$S/.ready" ] || { echo "нет $S/.ready" >&2; exit 4; }
[ -e "$SC" ] || { echo "нет сценария $SC" >&2; exit 4; }
need_next=0
compgen -G "$EH/root/*-$N.binlog" >/dev/null && need_next=1
if [ $need_next = 1 ] && [ ! -e "$SN/.ready" ]; then echo "нет $SN/.ready (довесок D+1)" >&2; exit 4; fi

V="$A/tk022/view/$D"
rm -rf "$V"; mkdir -p "$V/root-$D" "$V/carry" "$V/D20"
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
n="$(grep -o -F -e "--root $V/root-$D " -e "--touches-from $V/D20 " -e "--carry-root $V/carry " "$V/run.sh" | wc -l)"
[ "$n" = 9 ] || { echo "подмена путей: $n вместо 9" >&2; exit 6; }

if [ -n "${DRY:-}" ]; then echo "сухо: вид $V собран, подмена путей 9/9"; exit 0; fi
mkdir -p "$H/b5" "$A/tk022/status"
[ -e "$H/bin" ] || ln -s "$A/bin" "$H/bin"
# дом ворот (не e-<мес>): относительные --regime-from/--sigma-from ведут на study/ эпохи
if [ "$H" != "$EH" ]; then
  mkdir -p "$H/study"
  for x in $(grep -ohE 'study/[A-Za-z0-9_-]+' "$SC" | sort -u); do
    case "$x" in study/root-*|study/approaches) ;; *) [ -e "$H/$x" ] || ln -s "$EH/$x" "$H/$x" ;; esac
  done
fi
t0=$(date +%s)
( cd "$H" && bash "$V/run.sh" ) ; rc=$?
sec=$(( $(date +%s) - t0 ))
echo "rc=$rc sec=$sec"
[ "$H" = "$EH" ] || exit $rc
echo "rc=$rc sec=$sec" > "$A/tk022/status/$D.rc"
[ $rc = 0 ] || exit $rc

# .release: сутки X свободны, когда готов их счёт и счёт X−1 (X−1 брал X довеском); первые сутки — без X−1
ok() { grep -q '^rc=0 ' "$A/tk022/status/$1.rc" 2>/dev/null; }
rel() {
  local x="$1" p; p="$(date -u -d "$x -1 day" +%F)"
  if ok "$x" && { [ "$x" = 2026-01-01 ] || ok "$p"; }; then touch "$ST/$x/.release" 2>/dev/null || true; fi
}
rel "$D"; rel "$N"
rm -rf "$V"
exit 0
