#!/bin/bash
# файл из sofia-in переносится в каталог данных, только если sha256 = записанному в Софии; чужие/лежащие файлы не перезаписываются
H=$HOME/alpha-pump; IN=$H/sofia-in; n=0; bad=0
while read -r sha f; do
  f=${f#./}; src=$IN/$f
  case "$f" in candles/*|oi/*) ;; *) continue ;; esac
  [ -f "$src" ] || continue
  dst=$H/$f
  [ -e "$dst" ] && continue
  echo "$sha  $src" | sha256sum -c --status - || { bad=$((bad+1)); echo "SHA-РАСХОЖДЕНИЕ $f"; continue; }
  mkdir -p "$(dirname "$dst")"; cp "$src" "$dst.part" && mv "$dst.part" "$dst"; n=$((n+1))
  echo "$f" >> $H/log/sofia-merged.txt
done < $IN/MANIFEST.sha256
echo "merge: перенесено $n, расхождений $bad"
