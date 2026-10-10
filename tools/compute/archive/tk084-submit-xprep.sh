#!/bin/bash
# tk084-submit-xprep.sh: подача входа счёта XAU/CL (tk084-xprep.sh) в alsched; запуск: bash /data/tk084/submit-xprep.sh
mkdir -p /data/tk084/x
python3 /data/sched/alsched.py submit --cls prod --name tk084-xprep --max-runtime 6h --cores 8 --mem 24 -- bash /data/tk084/tk084-xprep.sh
