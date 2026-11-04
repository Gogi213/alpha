#!/bin/bash
H=$HOME/alpha-pump; IN=$H/sofia-in
while true; do
  rsync -a --partial --exclude='*.part' --exclude='*.tmp' -e "ssh -i $HOME/.ssh/id_sofia_pump -o BatchMode=yes -o StrictHostKeyChecking=accept-new" root@13.140.29.171:./ $IN/ 2>&1 | tail -3
  [ -f $IN/MANIFEST.sha256 ] && bash $H/bin/sofia-merge.sh
  sleep 120
done
