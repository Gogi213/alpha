#!/bin/bash
# TK-139 С-56: сборки /opt/alpha-compute/bin, не названные нигде (задания, Летопись, /data/tk*, репо)
set -e
O=/data/tk0139
ls /opt/alpha-compute/bin | grep '^alpha-' | sort -u > $O/bins.txt
{ grep -rhoE 'alpha-[A-Za-z0-9_.-]+' /data/sched/jobs /data/registry --include='*.json' --include='*.jsonl' --include='*.py' --include='*.sh' --include='*.csv' -I 2>/dev/null || true
  find /data/tk*/ -maxdepth 2 -type f -size -2M \( -name '*.sh' -o -name '*.py' -o -name '*.json' -o -name '*.jsonl' -o -name '*.txt' -o -name '*.cfg' \) -not -path "$O/*" -print0 2>/dev/null | xargs -0 -r grep -hoE 'alpha-[A-Za-z0-9_.-]+' -I 2>/dev/null || true
  grep -hoE 'alpha-[A-Za-z0-9_.-]+' /data/*.sh /data/sched/*.py 2>/dev/null || true
  cat $O/repo_tok.txt; } | sed "s/[.-]*$//" | sort -u > $O/mentioned.txt
comm -23 $O/bins.txt $O/mentioned.txt > $O/unused.txt
wc -l $O/bins.txt $O/mentioned.txt $O/unused.txt > $O/done.txt
