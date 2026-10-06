#!/usr/bin/env bash
# Гейт TK-064 на сервере счёта: флаг R1 выкл. байт в байт против b14 (base) + скорость; флаг вкл. — старые файлы побайтно.
#   tk064-gate.sh <BASE> <NEW> <каталог> <ЭПОХА>:<SYM>:<ДЕНЬ>... ; REPS=3 повторов замера (по одному, вперемешку). Запуск — через benchrun.sh stand.
set -uo pipefail
BASE=$1; NEW=$2; OUT=$3; shift 3; REPS=${REPS:-3}
FLAGS="--h3-mode notional --h3-usd 10000 --approach-bps 20"
mkdir -p "$OUT"; fail=0; TIMEFORMAT='%U %R'
run() { # $1 каталог, $2 бинарник, $3 корень, $4 SYM, $5 флаг
  mkdir -p "$1/mf"
  { time nice -n 5 "$2" lob touches --root "$3" --symbol "$4" $FLAGS --minute-flow "$1/mf" --out "$1/touches-$4.csv" $5 > "$1/log" 2>&1; echo $? > "$1/rc"; } 2> "$1/time"
  [ "$(cat "$1/rc")" = 0 ] || { echo "FAIL $1: $(tail -1 "$1/log")"; return 1; }
}
for c in "$@"; do
  IFS=: read -r ep sym day <<<"$c"; k="$sym-$day"; R="$OUT/root-$k"; rm -rf "$R"; mkdir -p "$R"
  for f in /data/alpha/epochs/$ep/root/*"$sym-$day"* /data/alpha/epochs/$ep/root/verify-"$sym".status; do ln -s "$f" "$R/"; done
  rm -rf "$OUT/$k"; run "$OUT/$k/base" "$BASE" "$R" "$sym" "" || { fail=1; continue; }
  run "$OUT/$k/off" "$NEW" "$R" "$sym" "" || { fail=1; continue; }
  run "$OUT/$k/on" "$NEW" "$R" "$sym" "--r1-cols" || { fail=1; continue; }
  n=0; bad=0
  for f in $(cd "$OUT/$k/base" && find . -type f ! -name log ! -name rc ! -name time | sort); do
    n=$((n+1)); cmp -s "$OUT/$k/base/$f" "$OUT/$k/off/$f" || { echo "DIFF off $k: $f"; bad=1; }
    case $f in *approaches*) ;; *) cmp -s "$OUT/$k/base/$f" "$OUT/$k/on/$f" || { echo "DIFF on $k: $f"; bad=1; } ;; esac
  done
  [ "$(cd "$OUT/$k/off" && find . -type f ! -name log ! -name rc ! -name time | wc -l)" = "$(cd "$OUT/$k/base" && find . -type f ! -name log ! -name rc ! -name time | wc -l)" ] || { echo "DIFF off $k: набор файлов"; bad=1; }
  [ $bad = 0 ] && echo "OK   $k: off — $n файлов побайтно; on — не-approaches побайтно"
  fail=$((fail|bad))
done
# approaches при on: прежние колонки те же (префикс столбцов), новых больше
for c in "$@"; do IFS=: read -r ep sym day <<<"$c"; k="$sym-$day"
  a="$OUT/$k/base/touches-$sym.csv"; b="$OUT/$k/on/touches-$sym.csv"; ls "$OUT/$k/base" | sed -n 1,3p >/dev/null
  for ab in $(cd "$OUT/$k/base" && find . -name '*approaches*'); do
    nb=$(head -1 "$OUT/$k/base/$ab" | awk -F, '{print NF}'); no=$(head -1 "$OUT/$k/on/$ab" | awk -F, '{print NF}')
    cmp -s <(cut -d, -f1-"$nb" "$OUT/$k/on/$ab") "$OUT/$k/base/$ab" && echo "OK   $k $ab: старые $nb колонок побайтно, на вкл. $no" || { echo "DIFF on $k $ab"; fail=1; }
  done
done
# скорость: base и off вперемешку на первом случае, сумма user-секунд, мин из REPS
IFS=: read -r ep sym day <<<"$1"; k="$sym-$day"; R="$OUT/root-$k"
for i in $(seq "$REPS"); do for v in base off; do b=$BASE; [ $v = off ] && b=$NEW; run "$OUT/t-$v-$i" "$b" "$R" "$sym" ""; done; done
for v in base off; do echo -n "TIME $v user,wall: "; for i in $(seq "$REPS"); do tr '\n' ' ' < "$OUT/t-$v-$i/time"; echo -n "| "; done; echo; done
echo "fail=$fail"; exit $fail
