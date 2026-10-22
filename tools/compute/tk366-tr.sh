#!/usr/bin/env bash
# TK-366 (G-016): дотяжка сделок после фазы закачки стакана (воркер tk366-dl.sh при флаге $D/skip-tr пишет ob-only).
#   tk366-tr.sh WAITPID [P=6] [ROUNDS=6] — ждёт конца процесса WAITPID (главный tk366-dl.sh), снимает флаг, качает недостающее
#   (стакан уже на диске — докачиваются только сделки) малым P; при 403 — пауза 10 мин и новый круг; в конце tr-phase.done и снят tr-phase.pending (импорт tk366-imp.sh ждёт, пока pending есть).
D=/srv/alpha-sda/tk366; W=$1; P=${2:-6}; R=${3:-6}
while kill -0 "$W" 2>/dev/null; do sleep 30; done
rm -f "$D/skip-tr"
for i in $(seq "$R"); do
  bash "$D/tk366-dl.sh" "$P"
  n=$(awk -F'\t' '$4 ~ /^fail:tr:403/ {k[$1 FS $2 FS $3]=1; next} $4=="dl"||$4 ~ /^missing/ {d[$1 FS $2 FS $3]=1} END{c=0; for(x in k) if(!(x in d)) c++; print c}' "$D/log/dl.tsv")
  echo "круг $i: без сделок (403) $n"; [ "$n" = 0 ] && break; sleep 600
done
echo "$(date +%FT%T) rounds=$i left=$n" > "$D/tr-phase.done"; rm -f "$D/tr-phase.pending"
