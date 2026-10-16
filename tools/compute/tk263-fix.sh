#!/usr/bin/env bash
# TK-263 п.3 (В-21): закрепление ускорения «сутки месяца на обоих дисках» в боевом пути формы.
#   1) откат-копия боевого day-cells.sh → /data/launch/prev-tk263; в /data/tk0231 — версия из репо с ALT_ROOT/ALT_JRN (умолчание /srv/alpha-sda/{root,jrn})
#   2) копия 14 суток марта (C14 из tk263-sda3.sh) /srv/tk0263/{root,root2,jrn} → /srv/alpha-sda/{root,jrn} (mv в одной ФС; исходники на sdb не трогаются)
#   3) гейт: мерка bs-launch.sh БЕЗ ALT_*/JRN (боевые умолчания, SCRIPTS=/data/tk0231), diff «байт в байт» с ref.md5; красный — автооткат day-cells.sh
#   /data/benchrun.sh wave bash tk263-fix.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>, NEW=<новый day-cells.sh>)
set -u
D=${D:-/data/tk0263/w5}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}; NEW=${NEW:-/data/tk0263/day-cells.new.sh}
A=/srv/tk0263; Z=/srv/alpha-sda; S=/data/tk0231; PREV=/data/launch/prev-tk263
mkdir -p "$D" "$PREV" "$Z/root" "$Z/jrn"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
if [ ! -e "$PREV/day-cells.sh" ]; then cp -a "$S/day-cells.sh" "$PREV/day-cells.sh"; md5sum "$S/day-cells.sh" > "$PREV/day-cells.md5"; fi
if [ ! -e "$Z/moved.done" ]; then
  for r in "$A/root" "$A/root2"; do for x in "$r"/root-*; do [ -d "$x" ] && mv "$x" "$Z/root/"; done; done
  for j in "$A"/jrn/*; do d=$(basename "$j"); [ -L "$j/wt" ] && continue; mkdir -p "$Z/jrn/$d"; mv "$j/wt" "$Z/jrn/$d/wt"; touch "$Z/jrn/$d/done"; done
  ls "$Z/root" | wc -l > "$Z/moved.done"
fi
cp "$NEW" "$S/day-cells.sh.new" && mv "$S/day-cells.sh.new" "$S/day-cells.sh"
SCRIPTS=$S NX=12 bash "$BS" pass "$D" G12 > "$D.G12.out" 2>&1
n=$(awk '$1=="diff_vs_ref_lines"{print $2}' "$D/pass-G12/metrics.txt" 2>/dev/null)
if [ "${n:-x}" != 0 ]; then cp -a "$PREV/day-cells.sh" "$S/day-cells.sh"; echo "ГЕЙТ КРАСНЫЙ (diff=${n:-нет}) — day-cells.sh откачен" > "$D.rollback"; fi
touch "$D.done"
