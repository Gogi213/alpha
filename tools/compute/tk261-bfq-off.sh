#!/bin/bash
# TK-261: возврат sda+sdb на mq-deadline (исходная настройка).
for d in sda sdb; do
  echo mq-deadline > /sys/block/$d/queue/scheduler
  echo "$d $(cat /sys/block/$d/queue/scheduler)"
done
