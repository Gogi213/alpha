#!/bin/bash
# tk065-g1-cmp.sh: разбор diff G1 по 31 суткам марта: различия только как «строки эталона TK-040 для монет слоя досчёта (TRX ATOM NEAR LIT XLM JUP HYPE PUMPFUN WIF PENDLE), которых нет в основном доме»?
# Для каждого различающегося файла: строки только у кандидата (<), строки только в эталоне (>) для монет слоя и для прочих. Итог /data/tk065/g1cmp-a.txt (последняя строка done).
SUP='^(TRXUSDT|ATOMUSDT|NEARUSDT|LITUSDT|XLMUSDT|JUPUSDT|HYPEUSDT|PUMPFUNUSDT|WIFUSDT|PENDLEUSDT),'
O=/data/tk065/g1cmp-a.txt; : > $O; nf=0; lt=0; gs=0; go=0; hdr=0
for d in /data/tk065/g1/2026-03-10; do
  while read -r sn rest; do
    a=$(echo "$rest" | sed 's/^Files \(.*\) and \(.*\) differ$/\1/'); b=$(echo "$rest" | sed 's/^Files \(.*\) and \(.*\) differ$/\2/')
    [ -f "$a" ] && [ -f "$b" ] || { echo "NOFILE $d $rest" >> $O; continue; }
    nf=$((nf+1)); r=$(diff "$a" "$b")
    l=$(echo "$r" | grep -c '^<'); s=$(echo "$r" | grep '^>' | sed 's/^> //' | grep -cE "$SUP"); t=$(echo "$r" | grep -c '^>'); lt=$((lt+l)); gs=$((gs+s)); go=$((go+t-s))
    [ "$l" != 0 ] || [ $((t-s)) != 0 ] && echo "DIFF $a l=$l ref_sup=$s ref_other=$((t-s))" >> $O
  done < $d/diff.txt
done
echo "files_differ $nf only_cand_lines $lt ref_only_sup_lines $gs ref_only_other_lines $go" >> $O
echo done >> $O
