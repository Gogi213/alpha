#!/usr/bin/env bash
# TK-264 (хвост TK-263, В-21): страж свежести копии суток на sda в боевом day-cells.sh.
#   1) откат-копия боевого day-cells.sh → /data/launch/prev-tk264; в /data/tk0231 — версия из репо (блок copy-fresh)
#   2) проверка на настоящих данных: 14 суток копии против sdb — все «свежие»; искусственно испорченные копии (в /data/tk0264/neg) — «УСТАРЕЛА»
#   3) гейт: мерка bs-launch.sh боевым путём (SCRIPTS=/data/tk0231, без ALT_*), diff «байт в байт» с ref.md5, ни одного stale-copy.txt; красный — автооткат
#   /data/benchrun.sh wave bash tk264-fix.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>, NEW=<новый day-cells.sh>)
set -u
D=${D:-/data/tk0264/w1}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}; NEW=${NEW:-/data/tk0264/day-cells.new.sh}
S=/data/tk0231; PREV=/data/launch/prev-tk264; Z=/srv/alpha-sda; E=/data/tk046/mar/home/alpha/epochs/e-mar/study; N=/data/tk0264/neg
mkdir -p "$D" "$PREV" "$N"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
if [ ! -e "$PREV/day-cells.sh" ]; then cp -a "$S/day-cells.sh" "$PREV/day-cells.sh"; md5sum "$S/day-cells.sh" > "$PREV/day-cells.md5"; fi
cp "$NEW" "$S/day-cells.sh.new" && mv "$S/day-cells.sh.new" "$S/day-cells.sh"; md5sum "$S/day-cells.sh" > "$D/day-cells.md5"
eval "$(sed -n '/copy-fresh:begin/,/copy-fresh:end/p' "$S/day-cells.sh")"
: > "$D/fresh-check.txt"; O=$D; ALERTS=/dev/null; first=
for d in $(awk '{print $2}' "$D/days.txt"); do
  [ -d "$Z/root/root-$d" ] || { echo "$d без копии" >> "$D/fresh-check.txt"; continue; }
  RS=$(readlink -f "$E/root-$d")
  r=$(root_fresh "$RS" "$Z/root/root-$d" && echo свежая || echo УСТАРЕЛА); j=$(jrn_fresh /data/p12r3a/jrn "$Z/jrn" && echo свежий || echo УСТАРЕЛ)
  echo "$d root:$r jrn:$j" >> "$D/fresh-check.txt"; [ -n "$first" ] || first=$d
done
d=$first; RS=$(readlink -f "$E/root-$d")   # негатив: копия суток с одним файлом нулевого размера / свежий wt
mkdir -p "$N/root/root-$d" "$N/jrn/$d/wt"; touch "$N/jrn/$d/done"
for f in "$Z/root/root-$d"/*; do ln -sf "$f" "$N/root/root-$d/$(basename "$f")"; done
b=$(ls "$Z/root/root-$d" | head -1); rm -f "$N/root/root-$d/$b"; : > "$N/root/root-$d/$b"
echo "негатив $d root:$(root_fresh "$RS" "$N/root/root-$d" && echo ОШИБКА-свежая || echo УСТАРЕЛА-ок) jrn:$(jrn_fresh /data/p12r3a/jrn "$N/jrn" && echo ОШИБКА-свежий || echo УСТАРЕЛ-ок)" >> "$D/fresh-check.txt"
SCRIPTS=$S NX=12 bash "$BS" pass "$D" G12 > "$D.G12.out" 2>&1
n=$(awk '$1=="diff_vs_ref_lines"{print $2}' "$D/pass-G12/metrics.txt" 2>/dev/null)
st=$(find "$D" -name stale-copy.txt | wc -l); echo "stale-copy.txt: $st" >> "$D/fresh-check.txt"
if [ "${n:-x}" != 0 ] || [ "$st" != 0 ] || [ "$(grep -v негатив "$D/fresh-check.txt" | grep -c УСТАРЕЛ)" != 0 ]; then cp -a "$PREV/day-cells.sh" "$S/day-cells.sh"; echo "ГЕЙТ КРАСНЫЙ (diff=${n:-нет} stale=$st) — day-cells.sh откачен" > "$D.rollback"; fi
touch "$D.done"
