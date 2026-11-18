#!/usr/bin/env bash
# П-42 §13.17 (TK-588): цепочка на srv — таблица эпизодов (sigfeat_run) → склейка (sigfeat_join). Метки НЕ читать до записи MDE в протокол.
# Запуск: systemd-run --unit=p42-t588-chain -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash tools/p42/sigfeat_chain.sh
# Готовность: файл /data/p42-t588/chain.done (код возврата). Далее — sigfeat_stats.py (после MDE).
cd /data/p42-t588/py
python3 sigfeat_run.py /data/p42-t588/out/eng /data/p42-t588 --procs 6 > /data/p42-t588/run.log 2>&1 && \
python3 sigfeat_join.py /data/p42-t588/out/eng /data/p42-t588 > /data/p42-t588/join.log 2>&1
echo $? > /data/p42-t588/chain.done
