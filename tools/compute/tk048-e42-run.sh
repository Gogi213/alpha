#!/usr/bin/env bash
# TK-048 К-4з: alpha-e42-idxr (индекс один на символо-сутки (Судья 05:20)), d15 боевыми флагами + ALPHA_HOLD_INDEX=1 + _CHECK=1; done: /data/tk048/e42-d15/done; в grid.log строка «индекс удержания»
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
B=/opt/alpha-compute/bin; n=alpha-e42-idxr
ssh "${K[@]}" $V "test -e /opt/alpha-compute/tk051-$n.done"
m=$(ssh "${K[@]}" $V "md5sum < $B/$n" | cut -d' ' -f1)
ssh "${K[@]}" $V "cat $B/$n" | ssh "${K[@]}" $C "cat > $B/$n.new && chmod +x $B/$n.new"
m2=$(ssh "${K[@]}" $C "md5sum < $B/$n.new" | cut -d' ' -f1)
[ "$m" = "$m2" ] || { echo "md5 не сошёлся"; exit 1; }
ssh "${K[@]}" $C "mv $B/$n.new $B/$n"
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e42-d15; rm -f /data/tk048/e42-d15/done
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1 ALPHA_HOLD_INDEX=1 ALPHA_HOLD_INDEX_CHECK=1"
cat > /data/tk048/e42-d15/run.sh <<EOS
#!/bin/bash
cd /data/tk051
/data/benchrun.sh stand bash stand.sh alpha-e42-idxr d15 TAG=e42 $F > /data/tk048/e42-d15/out.txt 2>&1
touch /data/tk048/e42-d15/done
EOS
systemctl reset-failed tk048-e42 2>/dev/null; systemd-run --quiet --unit=tk048-e42 --collect bash /data/tk048/e42-d15/run.sh && echo запущено'
