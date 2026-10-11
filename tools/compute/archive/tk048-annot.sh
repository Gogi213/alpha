#!/bin/bash
# TK-048: perf annotate всех символов perf-B.data (d15, b24c-pgo) в один файл; потом режем по заголовкам
D=/data/tk049/stand49fb/051317-d15-alpha-b24c-pgo; O=/data/tk048/annot; mkdir -p $O; rm -f $O/done
perf annotate -i $D/perf-B.data --stdio --stdio-color=never > $O/all.txt 2> $O/all.err
awk '/Source code & Disassembly/{n++} n<=8' $O/all.txt > $O/top8.txt
touch $O/done
