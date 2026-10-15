#!/bin/bash
# Замер на calc (TK-246): холодный старт и тёплое по /api/*, события. Запуск: ssh calc 'bash -s cold-page|cold-status|warm' < tk246_calc.sh
T=$(cat /etc/alpha-launch/token); B=http://127.0.0.1:8791/$T
t() { curl -s -o /dev/null -H 'Accept-Encoding: gzip' -w "$1 %{time_starttransfer}\n" "$B$1"; }
wait_up() { until curl -s -o /dev/null "$B/api/meta" --max-time 0.2 2>/dev/null; do :; done; }
case "$1" in
cold-page|cold-status)
  systemctl restart alpha-launch; s=$(date +%s.%N)
  until curl -s -o /dev/null --max-time 0.3 "http://127.0.0.1:8791/x/"; do sleep 0.02; done   # порт принимает (404 на неверный токен)
  echo "порт принял через $(echo "$(date +%s.%N) - $s" | bc) с"
  if [ "$1" = cold-page ]; then t /; t /api/status; else t /api/status; t /; fi
  t /api/runs; t /api/meta; t /api/cov; t /api/letopis ;;
warm) for i in 1 2 3 4 5; do for k in / /api/status /api/runs /api/meta /api/cov /api/letopis; do t $k; done; done ;;
esac
