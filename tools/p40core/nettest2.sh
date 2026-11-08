#!/bin/bash
# Замер чтения бокс -> сервер: P постоянных соединений, в каждом один rsync --files-from (без нового входа на файл).
# Использование (на сервере): bash nettest2.sh <список-файлов> <P> <T> [dest]
# Список — пути на боксе от корня (alpha/stakan/...). Байты = du каталога назначения (с недокачанными).
list=$1; P=$2; T=${3:-30}; dest=${4:-/dev/shm/nettest2}
rm -rf "$dest" /tmp/nt2_chunk_*; mkdir -p "$dest"
sed 's#^alpha/stakan/##' "$list" | split -n r/"$P" -d - /tmp/nt2_chunk_
s=$(date +%s%N)
for c in /tmp/nt2_chunk_*; do
  timeout "$T" rsync -q -R --files-from="$c" -e "ssh -c aes128-gcm@openssh.com -o Compression=no" box:alpha/stakan/ "$dest"/ 2>/dev/null &
done
wait
e=$(date +%s%N)
b=$(du -sb "$dest" | cut -f1)
n=$(find "$dest" -type f | wc -l)
ms=$(( (e-s)/1000000 ))
echo "P=$P окно=${T}с файлов=$n байт=$b время=${ms}мс МБ/с=$(awk -v b=$b -v m=$ms 'BEGIN{printf "%.1f", b/1048576/(m/1000)}')"
rm -rf "$dest" /tmp/nt2_chunk_*
