#!/bin/bash
# TK-415: подать закачку стаканов сентября (Gate и Bybit — разные хосты, два net-задания).
set -eu
python3 -c "import ast;ast.parse(open('/data/tk415/ob-dl.py').read())"
for s in gate bybit; do
  python3 /data/sched/alsched.py submit --kind net --cores 1 --max-runtime 28800 --est 7200 --name tk415-ob-$s -- \
    python3 /data/tk415/ob-dl.py $s /root/obsize-list.tsv /data/tk415/ob/$s /root/obsize-full.tsv,/tmp/obsize-full-rev.tsv
done
