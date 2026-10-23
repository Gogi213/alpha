#!/bin/bash
# TK-379: размер часового стакана фьючерса Gate по списку контрактов (HEAD, 4 потока).
# usage: tk379-ob-size.sh <список контрактов> <YYYYMMDDHH> <выход tsv: контракт<TAB>код<TAB>байт>
set -u
list=$1; hour=$2; out=$3
mon=${hour:0:6}
one() {
  c=$1; h=$2; m=$3
  curl -sI -m 30 "https://download.gatedata.org/futures_usdt/orderbooks/$m/$c-$h.csv.gz" | tr -d '\r' |
    awk -v c="$c" 'NR==1{code=$2} tolower($1)=="content-length:"{n=$2} END{printf "%s\t%s\t%s\n", c, code, n+0}'
}
export -f one
xargs -a "$list" -P4 -I{} bash -c "one {} $hour $mon" > "$out"
