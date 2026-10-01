#!/usr/bin/env bash
# TK-022: дом месяца янв–июн на VPS. Запуск с рабочей машины: tk022-vps-home.sh <jan|feb|mar|apr|may|jun>
# root/, study/{root-*,regime,klines,sigma240} — с деки тем же путём (ссылки на ящик абсолютные, на VPS /home/deck/sb -> /mnt/sb/alpha);
# D20 — ссылки на кэш подходов на ящике (derived/tk015/e-<мес>/D20, ~178 МБ/сутки, читается с ящика); сценарии суток — с деки.
set -euo pipefail
M="${1:?месяц}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
DECK=deck@192.168.1.49
VPS=root@13.140.29.171
ssh "${KEY[@]}" $DECK "cd ~/alpha/epochs && tar -cf - --exclude=e-$M/study/approaches --exclude=e-$M/study/touches e-$M/root e-$M/study" |
  ssh "${KEY[@]}" $VPS "mkdir -p /home/deck/alpha/epochs && tar -C /home/deck/alpha/epochs -xf -"
ssh "${KEY[@]}" $DECK "cd ~/alpha && tar -cf - tmp-p07/cells-by-day/jall-$M-2026-*" |
  ssh "${KEY[@]}" $VPS "mkdir -p /home/deck/alpha && tar -C /home/deck/alpha -xf -"
ssh "${KEY[@]}" $VPS "cd /home/deck/alpha/epochs/e-$M && ln -sfn /home/deck/alpha/bin bin && mkdir -p b5 study/approaches/D20 vps-status &&
  for d in /mnt/sb/alpha/derived/tk015/e-$M/D20/*/; do ln -sfn \"\${d%/}\" study/approaches/D20/\$(basename \"\$d\"); done
  echo $M: root \$(ls root | wc -l), study \$(ls study | wc -l), D20 \$(ls study/approaches/D20 | wc -l), сценариев \$(ls /home/deck/alpha/tmp-p07/cells-by-day/jall-$M-2026-*.sh | wc -l)"
