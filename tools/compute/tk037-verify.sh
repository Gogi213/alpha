#!/bin/bash
# TK-037: verify по монето-месяцам в /data/tk037/roots/e-YYYY-MM (маркер verify-<SYM>.status пишет сама lob verify). P — число параллельных.
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk035-steps}; R=${R:-/data/tk037/roots}; P=${P:-14}; LOGD=${LOGD:-/data/tk037/verify-logs}
mkdir -p "$LOGD"
for m in "$R"/e-*; do b=$(basename $m); ls "$m" | grep -a '\.binlog$' | sed 's/-20[0-9-]*\(-p[0-9]*\)\?\.binlog$//' | sort -u | while read s; do echo "$b $s"; done; done > "$LOGD/list.txt"
wc -l < "$LOGD/list.txt" > "$LOGD/count"
xargs -P "$P" -L1 bash -c 'b=$0; s=$1; t0=$(date +%s); '"$BIN"' lob verify --symbol $s --root '"$R"'/$b > '"$LOGD"'/$b-$s.log 2>&1; echo "$b $s rc=$? $(( $(date +%s)-t0 ))s" >> '"$LOGD"'/done.log' < "$LOGD/list.txt"
touch "$LOGD/ALL_DONE"
