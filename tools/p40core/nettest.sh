#!/bin/bash
# Замер чтения бокс -> сервер: P параллельных rsync по одному файлу, окно T секунд.
# Использование (на сервере): bash nettest.sh <список-файлов> <P> <T> [dest]
# Список — пути на боксе от корня (alpha/stakan/...). Принято: байты = du каталога назначения (с недокачанными).
list=$1; P=$2; T=${3:-30}; dest=${4:-/dev/shm/nettest}
rm -rf "$dest"; mkdir -p "$dest"
s=$(date +%s%N)
timeout "$T" xargs -a "$list" -P "$P" -I{} rsync -q -e "ssh -c aes128-gcm@openssh.com -o Compression=no" box:{} "$dest"/ 2>/dev/null
e=$(date +%s%N)
b=$(du -sb "$dest" | cut -f1)
n=$(ls "$dest" | wc -l)
ms=$(( (e-s)/1000000 ))
echo "P=$P окно=${T}с файлов=$n байт=$b время=${ms}мс МБ/с=$(awk -v b=$b -v m=$ms 'BEGIN{printf "%.1f", b/1048576/(m/1000)}')"
rm -rf "$dest"
