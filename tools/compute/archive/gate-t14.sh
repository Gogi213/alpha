#!/usr/bin/env bash
# Гейт T-14 «один проход» (`lob touches --levels-out`; условия Судьи 1–2,
# docs/research/reviews/one-pass-plan-2026-09-26.md):
#   OLD=bin/alpha-<старый> NEW=bin/alpha-<новый> gate-t14.sh <корень-суток>:<SYM> ...
# Корни — суточные (`study/root-<день>` эпохи); по Судье — монеты тяжёлая и лёгкая × август и сентябрь.
# На каждом (корень, монета) и окне `repeat_count` из WINDOWS (умолчание трекера 3600000 и 300000 —
# окно входит в CSV уровней) — боевые флаги ночных касаний (notional 10000, полоса 20, пол 900 с),
# четыре прогона разом:
#   old  — OLD `lob touches`;             new  — NEW `lob touches` без флага;
#   newl — NEW `lob touches --levels-out`; ref — OLD `lob levels` с теми же --h3-* и окном.
# Проверки побайтно: touches/approaches/mids1m у new и newl = old (флаг касаний не трогает);
# уровни newl = ref. Время new и newl — накладные `--levels-out`. Код выхода 1 при любом расхождении;
# каталог случая остаётся на диске только при расхождении (разбор), иначе удаляется (В-106).
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
OLD="${OLD:?старый бинарник}"; NEW="${NEW:?новый бинарник}"
OUT="${OUT:-$(mktemp -d)}"
WINDOWS="${WINDOWS:-3600000 300000}"
# У3: порог — _env.sh рядом со скриптом (копия гейта вне bin/ берёт его из $A/bin/).
ENV_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_env.sh"; [ -f "$ENV_SH" ] || ENV_SH="$A/bin/_env.sh"
# shellcheck source=_env.sh
source "$ENV_SH"
: "${ALPHA_H3:?_env.sh без констант У3 — выложить bin/_env.sh и bin/sets.txt}"
H3="$ALPHA_H3"
# BANDS — полоса подхода (умолчание — ночная, с полом 900 с); T-17 Р1 гоняет и без пола: BANDS="--approach-bps 20".
BANDS="${BANDS:---approach-bps 20 --approach-min-age-secs 900}"
cd "$A" || exit 1
[ -x "$OLD" ] && [ -x "$NEW" ] || { echo "нет исполняемых $OLD / $NEW"; exit 1; }
mkdir -p "$OUT" || exit 1
fail=0

timed() {  # $1 файл времени, дальше команда; stdout/stderr команды — в $1.log
  local tfile=$1; shift
  local t0; t0=$(date +%s%N)
  "$@" > "$tfile.log" 2>&1
  local rc=$?
  echo "$rc $(( ($(date +%s%N) - t0) / 1000000 ))" > "$tfile"
}

for spec in "$@"; do
  root=${spec%:*}; sym=${spec##*:}
  if [ ! -d "$root" ]; then echo "FAIL $spec: нет корня $root"; fail=1; continue; fi
  tag="$(basename "$root")-$sym"
  for w in $WINDOWS; do
    d="$OUT/$tag-w$w"
    rm -rf "$d"; mkdir -p "$d/old" "$d/new" "$d/newl" "$d/ref"
    # shellcheck disable=SC2086
    timed "$d/old.t" nice -n 15 "$OLD" lob touches --root "$root" --symbol "$sym" $H3 $BANDS \
      --repeat-window-ms "$w" --out "$d/old/touches-$sym.csv" &
    # shellcheck disable=SC2086
    timed "$d/new.t" nice -n 15 "$NEW" lob touches --root "$root" --symbol "$sym" $H3 $BANDS \
      --repeat-window-ms "$w" --out "$d/new/touches-$sym.csv" &
    # shellcheck disable=SC2086
    timed "$d/newl.t" nice -n 15 "$NEW" lob touches --root "$root" --symbol "$sym" $H3 $BANDS \
      --repeat-window-ms "$w" --out "$d/newl/touches-$sym.csv" --levels-out "$d/newl/levels-$sym.csv" &
    # shellcheck disable=SC2086
    timed "$d/ref.t" nice -n 15 "$OLD" lob levels --root "$root" --symbol "$sym" $H3 \
      --repeat-window-ms "$w" --out "$d/ref/levels-$sym.csv" &
    wait
    bad=0
    for r in old new newl ref; do
      read -r rc _ < "$d/$r.t"
      if [ "$rc" != 0 ]; then echo "FAIL $tag w=$w: прогон $r упал — $(tail -1 "$d/$r.t.log")"; bad=1; fi
    done
    if [ "$bad" = 0 ]; then
      for k in touches approaches mids1m; do
        for v in new newl; do
          if cmp -s "$d/old/$k-$sym.csv" "$d/$v/$k-$sym.csv"; then
            echo "OK   $tag w=$w $k $v = old ($(wc -l < "$d/old/$k-$sym.csv") строк)"
          else
            echo "DIFF $tag w=$w $k $v ≠ old"; bad=1
          fi
        done
      done
      if cmp -s "$d/ref/levels-$sym.csv" "$d/newl/levels-$sym.csv"; then
        echo "OK   $tag w=$w levels newl = lob levels old ($(wc -l < "$d/ref/levels-$sym.csv") строк)"
      else
        echo "DIFF $tag w=$w levels newl ≠ lob levels old"; bad=1
      fi
      read -r _ t_new < "$d/new.t"; read -r _ t_newl < "$d/newl.t"
      read -r _ t_ref < "$d/ref.t"
      echo "TIME $tag w=$w touches ${t_new} мс, touches+levels-out ${t_newl} мс, lob levels ${t_ref} мс (4 процесса разом)"
    fi
    if [ "$bad" = 0 ]; then rm -rf "$d"; else fail=1; echo "     разбор: $d"; fi
  done
done
[ "$fail" = 0 ] && echo "ГЕЙТ T-14: всё совпало" || echo "ГЕЙТ T-14: ЕСТЬ РАСХОЖДЕНИЯ"
exit "$fail"
