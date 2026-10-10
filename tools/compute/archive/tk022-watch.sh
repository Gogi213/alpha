#!/usr/bin/env bash
# TK-022: сторож счёта янв–июн на VPS. Запуск с рабочей машины (отдельным процессом): bash tools/compute/tk022-watch.sh
# Раз в 2 мин смотрит VPS. Ворота счёта — когда готовы сутки янв 03/05/06 (результат в data/tk022/gate.txt, тихо);
# `data/tk022/wake` (по нему диспетчер будит Исследователя) — только если: ворота не сошлись, сутки с rc≠0,
# все полосы умерли раньше срока или посчитаны все 181 суток (тогда ворота — повторно по всем сверяемым суткам).
cd "$(dirname "$0")/../.." || exit 1
mkdir -p data/tk022
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
V() { ssh "${KEY[@]}" root@13.140.29.171 "$@"; }
wake() { echo "$(date +%FT%T%z) $*" >> data/tk022/wake; }
gate_run() { V 'python3 /opt/alpha-compute/tk022/tk022-gate.py' > data/tk022/gate.txt 2>&1; }
gate_done=0
while [ ! -e data/tk022/wake ]; do
  r=$(V 'cd /home/deck/alpha/epochs; n=$(ls e-{jan,feb,mar,apr,may,jun}/vps-status/*.rc 2>/dev/null | wc -l); bad=$(grep -L "rc=0" e-{jan,feb,mar,apr,may,jun}/vps-status/*.rc 2>/dev/null | grep -v -E "^e-jan/vps-status/2026-01-01.rc$" | wc -l); g=0; for d in 2026-01-03 2026-01-05 2026-01-06; do [ -e e-jan/vps-status/$d.rc ] && g=$((g+1)); done; l=$(systemctl list-units "alpha-t22-*" --no-legend | grep -c running); echo "$n $bad $g $l"' 2>/dev/null) || { sleep 120; continue; }
  set -- $r
  n=$1 bad=$2 g=$3 lanes=$4
  echo "$(date +%T) готово $n/181, rc≠0: $bad, полос: $lanes" > data/tk022/progress.txt
  if [ "$bad" -gt 0 ]; then wake "сутки с rc≠0: $bad (готово $n)"; break; fi
  if [ "$gate_done" = 0 ] && [ "$g" -ge 3 ]; then
    if gate_run; then gate_done=1; else wake "ВОРОТА СЧЁТА НЕ СОШЛИСЬ — см. data/tk022/gate.txt"; break; fi
  fi
  if [ "$n" -ge 181 ]; then
    gate_run && wake "счёт янв–июн готов (181 суток), ворота ok" || wake "счёт готов, но ВОРОТА НЕ ПРОЙДЕНЫ — data/tk022/gate.txt"
    break
  fi
  if [ "$lanes" -eq 0 ]; then wake "все полосы остановились на $n/181"; break; fi
  sleep 120
done
