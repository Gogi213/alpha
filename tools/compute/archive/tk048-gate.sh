#!/bin/bash
# допуск единицы: ждать MemAvailable >= MEMGATE_KB; допуск по одному (flock), пауза 1 с, чтобы RSS успел вырасти
while [ "$(awk "/^MemAvailable/{print \$2}" /proc/meminfo)" -lt "$MEMGATE_KB" ]; do sleep 1; done
sleep 1
