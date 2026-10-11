#!/bin/bash
# tk084-p12-scan.sh: сутки с w-<сутки>.done, но без полных клеток в forms.csv (убиты OOM внутри юнита) -> /data/tk084/bad-days.txt; их .done уходит в /data/tk084/bad/ (не удаляется).
cd /data/tk084 || exit 2; mkdir -p bad; : > bad-days.txt.new
for f in w-20??-??-??.done; do
  d=${f#w-}; d=${d%.done}; [ -e days/p12-$d.cells ] || continue
  exp=$(grep -c . days/p12-$d.cells)
  got=$(cat w-$d/b5/p12/$d/*/forms.csv 2>/dev/null | awk -F, -v dd=$d '$2==dd{print $3}' | sort -u | grep -c .)
  [ "$got" = "$exp" ] && continue
  echo "$d $got/$exp" >> bad-days.txt.new; mv $f bad/$f
done
mv bad-days.txt.new bad-days.txt; touch p12-scan.done
