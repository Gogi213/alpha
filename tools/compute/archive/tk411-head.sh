#!/bin/bash
# TK-411: HEAD месячного архива сделок Gate -> "рынок<TAB>инструмент<TAB>http<TAB>байт". Вход stdin: "рынок инструмент url"; xargs -P N -L1 bash tk411-head.sh
kind=$1; c=$2; url=$3
curl -sI -m 20 --retry 2 "$url" | tr -d '\r' | awk -v k="$kind" -v c="$c" 'BEGIN{FS=": "} /^HTTP/{h=$1" "$2} tolower($1)=="content-length"{l=$2} END{split(h,a," "); print k"\t"c"\t"a[2]"\t"l}'
