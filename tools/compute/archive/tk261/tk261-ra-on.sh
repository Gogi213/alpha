#!/bin/bash
# TK-261 Ш2.4: read_ahead_kb sda+sdb 65536 -> 512. Возврат — tk261-ra-off.sh.
for d in sda sdb; do echo 512 > /sys/block/$d/queue/read_ahead_kb; echo "$d read_ahead_kb=$(cat /sys/block/$d/queue/read_ahead_kb)"; done
