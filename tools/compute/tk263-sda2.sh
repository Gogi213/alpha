#!/usr/bin/env bash
# TK-263 п.2б (В-21): Ш2 копировал лишь wt журнала — root-<d> на sdb состоит из симлинков на бинлоги /data/alpha/epochs/e-mar/root/*, их читает путь формы.
#   Здесь root-<d> половины суток (АББА, как в Ш2) копируется с разыменованием (cp -L: сами бинлоги + .events) на sda /srv/tk0263/root2, wt — копия Ш2.
#   /data/benchrun.sh wave bash tk263-sda2.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>, STEPS="метка:NX" )
set -u
D=${D:-/data/tk0263/w3}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}; A=/srv/tk0263
E=/data/tk046/mar/home/alpha/epochs/e-mar/study
mkdir -p "$D" "$A/root2"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
if [ ! -e "$A/copy2.done" ]; then
  t0=$(date +%s)
  while read -r d; do
    [ -e "$A/jrn/$d/wt" ] || { echo "нет копии wt $d" > "$A/copy2.fail"; exit 3; }
    mkdir -p "$A/root2/root-$d"; cp -rL --preserve=timestamps "$(readlink -f "$E/root-$d")/." "$A/root2/root-$d/"
  done < "$A/sda-days.txt"
  while read -r d; do
    s=$(readlink -f "$E/root-$d"); c=$A/root2/root-$d
    a=$(cd "$s" && find -L . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
    b=$(cd "$c" && find . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
    echo "$d $a $b $([ "$a" = "$b" ] && echo ok || echo РАЗНИЦА)"
  done < "$A/sda-days.txt" > "$A/copy2-check.txt"
  echo "copy2_s $(( $(date +%s) - t0 ))" > "$A/copy2.done"
  awk '{s+=$3}END{print "copy2_bytes", s}' "$A/copy2-check.txt" >> "$A/copy2.done"
fi
for st in ${STEPS:-C12:12}; do
  lab=${st%%:*}; nx=${st#*:}
  SCRIPTS=$A/scripts ALT_ROOT=$A/root2 JRN=$A/jrn NX=$nx bash "$BS" pass "$D" "$lab" > "$D.$lab.out" 2>&1
done
touch "$D.done"
