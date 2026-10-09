#!/bin/bash
python3 /data/sched/alsched.py submit --cls prod --name tk065-g1cmp --max-runtime 1h --cores 1 --mem 1 -- bash /data/tk065/g1-cmp.sh
