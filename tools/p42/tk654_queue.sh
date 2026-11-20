#!/usr/bin/env bash
# очередь: ждать, пока загрузка srv (1 мин) не опустится ниже 7,4 из 8 ядер (≈ 93 %), затем пакет P&L
while [ "$(awk '{print ($1 > 7.4)}' /proc/loadavg)" = 1 ]; do sleep 60; done
exec bash /data/p42-tk654/code/tk654_pnl.sh
