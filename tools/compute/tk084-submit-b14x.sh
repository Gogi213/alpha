#!/bin/bash
# подача счёта b14 по XAU/CL в alsched; запуск: bash /data/tk084/submit-b14x.sh (после /data/tk084/x/xprep.done)
python3 /data/sched/alsched.py submit --cls prod --name tk084-b14x --max-runtime 8h --cores 8 --mem 24 -- env P=8 bash /data/tk084/tk084-b14x.sh
