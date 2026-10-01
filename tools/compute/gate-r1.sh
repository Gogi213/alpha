#!/usr/bin/env bash
# Гейт TK-025 (R1, `--r1-cols`) на Steam Deck: байт в байт против прежнего бинарника + скорость.
#   OLD=bin/alpha-<старый> NEW=bin/alpha-<новый> OUT=<каталог> [REPS=3] gate-r1.sh <корень>:<SYM>[:t] ...
# Метка `:t` — случай ещё и для замера скорости (REPS повторов, прогоны ПООЧЕРЁДНО, по одному — эталон с
# занятой деки врёт). Флаги — как у боевого кэша авг/сен (`lob touches`, нотионал 10 000, полоса 20, минутные потоки).
# По случаю: old = OLD без флага; off = NEW без флага (все файлы побайтно = old); on = NEW с `--r1-cols`
# (touches/минутные потоки побайтно = old; approaches — прежние колонки те же + 62 новых, gate-r1-cols.py).
# Результат — построчно OK/DIFF/TIME; код выхода 1 при любом расхождении.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
OLD="${OLD:?старый бинарник}"; NEW="${NEW:?новый бинарник}"; OUT="${OUT:?каталог результатов}"
REPS="${REPS:-3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAGS="--h3-mode notional --h3-usd 10000 --approach-bps 20"
cd "$A" || exit 1
[ -x "$OLD" ] && [ -x "$NEW" ] || { echo "нет исполняемых $OLD / $NEW"; exit 1; }
mkdir -p "$OUT" || exit 1
fail=0
TIMEFORMAT='%U %R'

run() {  # $1 каталог прогона, $2 бинарник, $3 корень, $4 монета, $5 доп. флаг
  local d=$1
  mkdir -p "$d/mf"
  # shellcheck disable=SC2086
  { time nice -n 5 "$2" lob touches --root "$3" --symbol "$4" $FLAGS --minute-flow "$d/mf" --out "$d/touches-$4.csv" $5 > "$d/log" 2>&1; echo $? > "$d/rc"; } 2> "$d/time"
  [ "$(cat "$d/rc")" = 0 ] || { echo "FAIL $d: прогон упал — $(tail -1 "$d/log")"; return 1; }
}

same_dir() {  # $1 эталон, $2 проверяемый, $3 имя, остальное — исключённые имена файлов
  local a=$1 b=$2 name=$3; shift 3
  local f n=0 bad=0 skip
  for f in $(cd "$a" && find . -type f ! -name log ! -name rc ! -name time | sort); do
    skip=0; for x in "$@"; do [ "$(basename "$f")" = "$x" ] && skip=1; done
    [ "$skip" = 1 ] && continue
    n=$((n + 1))
    cmp -s "$a/$f" "$b/$f" || { echo "DIFF $name: $f"; bad=1; }
  done
  [ "$(cd "$b" && find . -type f ! -name log ! -name rc ! -name time | wc -l)" = "$(cd "$a" && find . -type f ! -name log ! -name rc ! -name time | wc -l)" ] || { echo "DIFF $name: набор файлов другой"; bad=1; }
  [ "$bad" = 0 ] && echo "OK   $name: $n файлов побайтно"
  return "$bad"
}

user_min() {  # минимум user-секунд по $@ (файлы time)
  cat "$@" | awk '{print $1}' | sort -g | head -1
}

for spec in "$@"; do
  timing=0; case "$spec" in *:t) timing=1; spec=${spec%:t};; esac
  root=${spec%:*}; sym=${spec##*:}
  tag="$(basename "$root")-$sym"
  if [ ! -d "$root" ]; then echo "FAIL $tag: нет корня $root"; fail=1; continue; fi
  n=1; [ "$timing" = 1 ] && n=$REPS
  ons=()
  for i in $(seq 1 "$n"); do
    run "$OUT/$tag/old$i" "$OLD" "$root" "$sym" "" || { fail=1; break; }
    run "$OUT/$tag/off$i" "$NEW" "$root" "$sym" "" || { fail=1; break; }
    run "$OUT/$tag/on$i" "$NEW" "$root" "$sym" "--r1-cols" || { fail=1; break; }
    ons+=("$OUT/$tag/on$i")
  done
  [ -d "$OUT/$tag/on1" ] || continue
  # выход NEW без флага и OLD — все файлы побайтно (по всем повторам)
  for i in $(seq 1 "$n"); do
    same_dir "$OUT/$tag/old1" "$OUT/$tag/off$i" "$tag off$i = old1" || fail=1
  done
  # со флагом: всё, кроме approaches, побайтно; approaches — прежние колонки + 62 новых
  same_dir "$OUT/$tag/old1" "$OUT/$tag/on1" "$tag on1 = old1 (кроме approaches)" "approaches-$sym.csv" || fail=1
  if python3 "$HERE/gate-r1-cols.py" "$OUT/$tag/old1/approaches-$sym.csv" "$OUT/$tag/on1/approaches-$sym.csv"; then :; else echo "DIFF $tag approaches on1"; fail=1; fi
  for i in $(seq 2 "$n"); do cmp -s "$OUT/$tag/on1/approaches-$sym.csv" "$OUT/$tag/on$i/approaches-$sym.csv" || { echo "DIFF $tag on$i ≠ on1 (повтор)"; fail=1; }; done
  echo "ROWS $tag: approaches $(($(wc -l < "$OUT/$tag/old1/approaches-$sym.csv") - 1)) строк, touches $(($(wc -l < "$OUT/$tag/old1/touches-$sym.csv") - 1))"
  if [ "$timing" = 1 ]; then
    o=$(user_min "$OUT/$tag"/old*/time); f=$(user_min "$OUT/$tag"/off*/time); r=$(user_min "$OUT/$tag"/on*/time)
    echo "TIME $tag user-мин из $n: old $o с · off $f с ($(awk -v a="$o" -v b="$f" 'BEGIN{if(a>0)printf "%+.2f", (b-a)/a*100; else printf "n/a"}') %) · on $r с ($(awk -v a="$o" -v b="$r" 'BEGIN{if(a>0)printf "%+.2f", (b-a)/a*100; else printf "n/a"}') %)"
  fi
done
[ "$fail" = 0 ] && echo "ГЕЙТ R1: всё совпало" || echo "ГЕЙТ R1: ЕСТЬ РАСХОЖДЕНИЯ"
exit "$fail"
