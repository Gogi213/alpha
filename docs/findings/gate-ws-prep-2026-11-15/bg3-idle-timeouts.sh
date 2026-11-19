#!/bin/bash
cd /data/gate-probe
Q='[{"time":1,"channel":"futures.trades","event":"subscribe","payload":["BLUEFOCUS_USDT","TMO_USDT","LQD_USDT"]}]'
systemd-run --quiet --unit=gate-dead-silent --collect -p WorkingDirectory=/data/gate-probe python3 gate_rawws.py 150 frames/p-dead-silent.jsonl silent "$Q"
systemd-run --quiet --unit=gate-dead-wsping --collect -p WorkingDirectory=/data/gate-probe python3 gate_rawws.py 150 frames/p-dead-wsping.jsonl wsping "$Q"
systemd-run --quiet --unit=gate-dead-appping --collect -p WorkingDirectory=/data/gate-probe python3 gate_rawws.py 150 frames/p-dead-appping.jsonl appping "$Q"
