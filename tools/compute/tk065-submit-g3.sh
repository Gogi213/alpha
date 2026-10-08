#!/bin/bash
python3 /data/sched/alsched.py submit --cls prod --name tk065-g3 --max-runtime 3h --cores 1 --mem 3 -- bash /data/tk065/g3.sh
