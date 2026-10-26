#!/bin/bash
# TK-410 П-31 W8: пакетное извлечение середины 193 пулов из sqd10. Метка extall.done
cd /data/tk0393/tk410
/data/tk0380/npvenv/bin/python tk410-extract-all.py missing-pools.csv mid-all > extall.log 2>&1
echo rc=$? >> extall.log; touch extall.done
