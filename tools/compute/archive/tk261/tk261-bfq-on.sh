#!/bin/bash
# TK-261 Ш2.1: sda+sdb -> bfq, low_latency=0 (остальное по умолчанию). Возврат — tk261-bfq-off.sh.
modprobe bfq || exit 1
for d in sda sdb; do
  echo bfq > /sys/block/$d/queue/scheduler
  echo 0 > /sys/block/$d/queue/iosched/low_latency
  echo "$d $(cat /sys/block/$d/queue/scheduler) low_latency=$(cat /sys/block/$d/queue/iosched/low_latency) slice_idle=$(cat /sys/block/$d/queue/iosched/slice_idle) max_budget=$(cat /sys/block/$d/queue/iosched/max_budget)"
done
