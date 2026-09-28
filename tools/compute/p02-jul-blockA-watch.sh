#!/usr/bin/env bash
# TK-018: сторож блока A П-02 на июле — ждёт метки <день>.done у всех 31 суток, затем сборщик; итог — строка в
# ~/alpha/tmp-p02jul/blockA.finished (`rc=<код> <время>` или `rc=incomplete …`, если заданий p02jul в очереди больше нет,
# а суток меньше 31). Запуск: systemd-run --user --unit=alpha-p02jul-collect bash ~/alpha/tmp-p02jul/p02-jul-blockA-watch.sh
set -uo pipefail
H="$HOME/alpha"
T="$H/tmp-p02jul"
J="$H/epochs/e-jul/study/p02jul"
Q="$H/queue"
while :; do
  n=$(ls "$J"/2026-07-??.done 2>/dev/null | wc -l)
  [ "$n" -ge 31 ] && break
  left=$(ls "$Q"/pending/*-p02jul-*.job "$Q"/running/*-p02jul-* 2>/dev/null | wc -l)
  # сутки VPS (07-21…31) приходят через ящик или rrsync (vpspull) — пока приёмщик жив, ждать
  systemctl --user is-active -q alpha-p02jul-recv alpha-p02jul-probe alpha-p02jul-vpspull && left=$((left + 1))
  if [ "$left" -eq 0 ]; then
    echo "rc=incomplete суток=$n $(date -u +%FT%TZ)" > "$T/blockA.finished"
    exit 1
  fi
  sleep 300
done
bash "$T/p02-jul-blockA-collect.sh" > "$T/collect.log" 2>&1
rc=$?
echo "rc=$rc $(date -u +%FT%TZ)" > "$T/blockA.finished"
exit $rc
