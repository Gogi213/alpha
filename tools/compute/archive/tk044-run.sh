#!/usr/bin/env bash
# TK-044: окна минута/час по всем (корень, монета) из списка units.txt (строки: каталог монета флаг57); битые первыми.
# Запуск на сервере счёта через systemd-run (CPUQuota ≤ 1500 %); докачка — готовые w-*.csv пропускаются.
set -u
B=/opt/alpha-compute/bin; T=/data/tk044/run; U=${1:-$T/units.txt}; P=${2:-8}
mkdir -p "$T"; cd "$T" || exit 1
one() {
  d=$1; s=$2; tag=$(echo "$d" | sed 's#^/data/##; s#/#_#g')
  f="w-$tag-$s.csv"
  [ -s "$f" ] && exit 0
  "$B/alpha-tk044c" lob verify --symbol "$s" --root "$d" --keep-going --windows-out "$f.tmp" > "w-$tag-$s.out" 2>&1
  [ -s "$f.tmp" ] && mv "$f.tmp" "$f" || echo "FAIL $d $s" >> failed.txt
}
export -f one; export B
total=$(wc -l < "$U")
( while :; do
    n=$(ls w-*.csv 2>/dev/null | wc -l)
    printf '{"ticket":"TK-044","step":"окна по всем суткам","done":%s,"total":%s,"unit":"монета×корень","next":"гейт","updated":"%s"}\n' "$n" "$total" "$(date -Is)" > /data/progress/tk044.json.tmp
    mv /data/progress/tk044.json.tmp /data/progress/tk044.json
    [ "$n" -ge "$total" ] && break; sleep 30
  done ) &
awk '{print $1" "$2}' "$U" | xargs -P "$P" -L1 bash -c 'one "$0" "$1"'
wait
