#!/bin/bash
# TK-410 проба Q: настоящая середина и ликвидность пула Q (BSC) из сырых логов sqd
B=/data/tk0393/tk410; rm -f $B/Q.done
/data/tk0380/npvenv/bin/python /data/tk0393/tk410-extract.py Q bsc 0x8Bafe0bdD3eB9ae0539f5B32e771C1a72A189b7f $B/Q-raw.csv > $B/Q-extract.log 2>&1
echo rc=$? >> $B/Q-extract.log; touch $B/Q.done
