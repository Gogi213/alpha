#!/bin/bash
# tk065-wave-day.sh <сутки> <мес> <бинарник>: основной r2 и затем r2t суток одним заданием (выход /data/tk065/w-<сутки>, wt-<сутки>)
d=$1; M=$2; B=$3
SCR=r2 bash /data/tk065/probe.sh w-$d $B $M $d
SCR=r2t bash /data/tk065/probe.sh wt-$d $B $M $d
