#!/bin/bash
# TK-410 проба карты на B2: Q1 по событиям. Метка B2map.done
B=/data/tk0393/tk410; rm -f $B/B2map.done
GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python /data/tk0393/tk410-map.py B2 $B/B2-raw.csv $B/B2map > $B/B2map.log 2>&1
echo rc=$? >> $B/B2map.log; touch $B/B2map.done
