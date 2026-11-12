#!/bin/bash
cd /data/tk0380/tk392; PY=/data/tk0380/npvenv/bin/python
for L in 4 5; do $PY tk392-agg.py curve$L agg$L.csv; $PY tk392-final2.py curve$L holm$L.csv share$L.csv; done
echo DONE > fin45.done
