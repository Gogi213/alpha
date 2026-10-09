#!/bin/bash
# TK-037: добор по списку need-r4.txt на полной параллельности, затем перенос бинлогов sda -> sdb (/data/tk037/roots)
export RAW=/root/tk037raw ROOT=/data/tk037/roots LOG=/data/tk037/one-r4.log NC=0
xargs -P 10 -L1 /root/tk037-one.sh < /data/tk037/need-r4.txt
touch /data/tk037/r4.done
for m in /root/tk037roots/e-*; do b=$(basename $m); mkdir -p /data/tk037/roots/$b; find $m -type f -print0 | xargs -0 -P 3 -I{} mv -n {} /data/tk037/roots/$b/; done
touch /data/tk037/move.done
