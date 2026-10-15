#!/bin/bash
# TK-261: возврат read_ahead_kb sda+sdb на исходные 65536.
for d in sda sdb; do echo 65536 > /sys/block/$d/queue/read_ahead_kb; echo "$d read_ahead_kb=$(cat /sys/block/$d/queue/read_ahead_kb)"; done
