#!/usr/bin/env bash
# TK-048 К-4д: ABBA d15 боевых флагов (с SKIP_SAME) ± ALPHA_HOLD_INDEX=1, бинарник alpha-e36-idx (на calc есть).
#   done: /data/tk048/e37-d15/done, out.txt (пара 1: A=без, B=с), outr.txt (пара 2, порядок обратный); metrics.txt: wall/user, gate files 729 diff 0
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e37-d15; rm -f /data/tk048/e37-d15/done
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1"
cat > /data/tk048/e37-d15/run.sh <<EOS
#!/bin/bash
cd /data/tk051
B=alpha-e36-idx
pair() { # $1 метка, $2 первый арм (A|B)
  local a="bash /data/tk051/stand.sh \$B d15 TAG=\$1-A $F" b="bash /data/tk051/stand.sh \$B d15 TAG=\$1-B $F ALPHA_HOLD_INDEX=1"
  if [ \$2 = A ]; then \$a & sleep 2; \$b & else \$b & sleep 2; \$a & fi; wait
}
/data/benchrun.sh stand bash -c "\$(declare -f pair); B=\$B; pair p1 A" > /data/tk048/e37-d15/out.txt 2>&1
/data/benchrun.sh stand bash -c "\$(declare -f pair); B=\$B; pair p2 B" > /data/tk048/e37-d15/outr.txt 2>&1
touch /data/tk048/e37-d15/done
EOS
systemctl reset-failed tk048-e37 2>/dev/null; systemd-run --quiet --unit=tk048-e37 --collect bash /data/tk048/e37-d15/run.sh && echo запущено'
