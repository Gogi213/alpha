#!/bin/bash
# TK-385 y7: кэш событий ZEC,Q (15 процессов) → сим по (монета,форма,k×T) на 15 воркерах. Память воркера мала (mmap).
cd /data/tk0380; mkdir -p tk385/y7
for c in ZEC Q; do [ -f tk385/cache/$c/DONE ] || npvenv/bin/python tk385-prep6.py $c 15 > tk385/y7/prep-$c.log 2>&1; done
for c in ZEC Q; do for f in sym asym spr; do for ki in 0 1 2 3 4 5; do echo "$c $f $ki"; done; done; done > tk385/y7-jobs.txt
xargs -P 15 -L 1 bash -c 'KI=$2 npvenv/bin/python tk385-yorsh6.py $0 bsc med $1 tk385/y7/$0-$1-$2.csv > tk385/y7/$0-$1-$2.log 2>&1' < tk385/y7-jobs.txt
touch tk385/y7.done
