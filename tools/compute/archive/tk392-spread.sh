#!/bin/bash
cd /data/tk0380 && /data/tk0380/npvenv/bin/python tk392/tk392-spread.py tk392/curveall bbo tk392/spread.csv tk392/slices-spread.csv > tk392/spread.log 2>&1 && echo DONE > tk392/spread.done
