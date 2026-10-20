#!/usr/bin/env bash
# TK-263 п.3 (В-21), повтор: в tk263-fix.sh mv root2→/srv/alpha-sda/root упал на 12 сутках (там уже лежали маленькие
# root-<d> из Ш2: симлинки+events) и гейт прошёл на неполной раскладке (sda 2,3 ГБ, не ≈6). Здесь: мелкие копии Ш2 → в
# /srv/alpha-sda/stale (mv, не rm), root2/root-<d> с бинлогами → /srv/alpha-sda/root; гейт боевым путём без ALT_*/JRN.
#   /data/benchrun.sh wave bash tk263-fix2.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>)
set -u
D=${D:-/data/tk0263/w6}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}
A=/srv/tk0263; Z=/srv/alpha-sda; S=/data/tk0231; PREV=/data/launch/prev-tk263
mkdir -p "$D" "$Z/stale"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
for x in "$A"/root2/root-*; do
  [ -d "$x" ] || continue; b=$(basename "$x")
  [ -e "$Z/root/$b" ] && mv "$Z/root/$b" "$Z/stale/$b"
  mv "$x" "$Z/root/$b"
done
ls "$A"/root2 | wc -l > "$D/root2-left.txt"
SCRIPTS=$S NX=${NX:-15} bash "$BS" pass "$D" G12 > "$D.G12.out" 2>&1
n=$(awk '$1=="diff_vs_ref_lines"{print $2}' "$D/pass-G12/metrics.txt" 2>/dev/null)
if [ "${n:-x}" != 0 ]; then cp -a "$PREV/day-cells.sh" "$S/day-cells.sh"; echo "ГЕЙТ КРАСНЫЙ (diff=${n:-нет}) — day-cells.sh откачен" > "$D.rollback"; fi
touch "$D.done"
