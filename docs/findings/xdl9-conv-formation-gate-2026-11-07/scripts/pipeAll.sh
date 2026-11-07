#!/bin/bash
D=$(dirname "$(readlink -f "$0")")
bash $D/pipeG.sh || exit 1
bash $D/pipeQ.sh out out_noq5 --with-gate q || exit 1
bash $D/pipeN.sh || exit 1
bash $D/pipeQ.sh out_nogate out_nogate_noq5 --no-gate nq || exit 1
echo "ALLDONE $(date -u +%FT%TZ)" >> $HOME/xdl9/c2g/logs/pipe.status
