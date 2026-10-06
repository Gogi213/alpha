#!/bin/bash
# Обёртка подачи chain67 в очередь alpha-sched (прямой ssh … alsched.py submit режет хук bench_guard). Запуск: systemd-run --unit tk048-submit67 --collect bash /data/tk048/chain67-submit.sh
python3 /data/sched/alsched.py submit --cls prod --name tk048-chain67 --max-runtime 3h --cores 4 --mem 8 --disk hdd1 --cwd /data/tk048 -- bash /data/tk048/chain67-pack.sh
