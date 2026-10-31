#!/bin/bash
PY=/data/tk421/venv/bin/python
$PY -I /data/tk459/sm_s4.py /data/tk0451/l2 /srv/alpha-sda/candles-1m/binance-um /data/tk459/s4 15 > /data/tk459/s4.log 2>&1
touch /data/tk459/DONE_S4
