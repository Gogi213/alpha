#!/usr/bin/env bash
# TK-263 п.2в (В-21): C12 (половина суток с бинлогами на sda) дал 145,4 с против A 210,05 — sda 5,2 ГБ / sdb 8,8 ГБ, не поровну.
#   Здесь: повтор C12 (шум) → докопировать на sda ещё 2 тяжёлых суток из sdb-половины (root-<d> cp -L + wt) → C14 (14 суток на sda).
#   /data/benchrun.sh wave bash tk263-sda3.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>)
set -u
D=${D:-/data/tk0263/w4}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}; A=/srv/tk0263
E=/data/tk046/mar/home/alpha/epochs/e-mar/study; JS=/data/p12r3a/jrn
mkdir -p "$D" "$A/root2"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
run() { SCRIPTS=$A/scripts ALT_ROOT=$A/root2 JRN=$A/jrn NX=${NX:-15} bash "$BS" pass "$D" "$1" > "$D.$1.out" 2>&1; }
run C12b
if [ ! -e "$A/copy3.done" ]; then
  t0=$(date +%s); : > "$A/extra-days.txt"
  # сутки sdb-половины (NR%4 in {2,3} по day-order), две самые тяжёлые — позиции 2 и 6
  awk '{print NR, $2}' "$D/days.txt" | awk '$1==2||$1==6{print $2}' > "$A/extra-days.txt"
  while read -r d; do
    [ -e "$A/root2/root-$d" ] && { echo "$d уже на sda" > "$A/copy3.fail"; exit 3; }
    mkdir -p "$A/root2/root-$d"; cp -rL --preserve=timestamps "$(readlink -f "$E/root-$d")/." "$A/root2/root-$d/"
    unlink "$A/jrn/$d/wt"; cp -a "$JS/$d/wt" "$A/jrn/$d/wt"
  done < "$A/extra-days.txt"
  while read -r d; do
    for p in "$(readlink -f "$E/root-$d")|$A/root2/root-$d" "$JS/$d/wt|$A/jrn/$d/wt"; do
      s=${p%|*}; c=${p#*|}
      a=$(cd "$s" && find -L . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
      b=$(cd "$c" && find -L . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
      echo "$d $a $b $([ "$a" = "$b" ] && echo ok || echo РАЗНИЦА)"
    done
  done < "$A/extra-days.txt" > "$A/copy3-check.txt"
  echo "copy3_s $(( $(date +%s) - t0 ))" > "$A/copy3.done"
fi
run C14
touch "$D.done"
