#!/bin/bash
# TK-385 y8: ZEC — пересборка кэша (в y7 остался кэш пробы на 3 ч с меткой DONE → падение) → 18 заданий на 15 воркерах.
cd /data/tk0380; mkdir -p tk385/y8
rm -rf tk385/cache/ZEC
npvenv/bin/python tk385-prep6.py ZEC 15 > tk385/y8/prep-ZEC.log 2>&1
grep '^ZEC' tk385/y7-jobs.txt > tk385/y8-jobs.txt
xargs -P 15 -L 1 bash -c 'KI=$2 npvenv/bin/python tk385-yorsh6.py $0 bsc med $1 tk385/y7/$0-$1-$2.csv > tk385/y7/$0-$1-$2.log 2>&1' < tk385/y8-jobs.txt
touch tk385/y8.done
