#!/usr/bin/env bash
# TK-048: e26-pgo = e22+e25 + PGO (профиль переобучен на этом коде); пара d15 против боевого e17b-pgo в обоих порядках, боевые флаги как в обучении
# done: /data/tk048/e26-d15/done, результат out.txt (e17b,e26) и outr.txt (e26,e17b)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); C=root@89.163.242.211
ssh "${K[@]}" $C 'mkdir -p /data/tk048/e26-d15; rm -f /data/tk048/e26-d15/done
F="ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_SIG_CACHE=3000000 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1"
cat > /data/tk048/e26-d15/run.sh <<EOS
#!/bin/bash
cd /data/tk051
/data/benchrun.sh stand bash stand.sh pair alpha-e17b-pgo alpha-e26-pgo d15 $F > /data/tk048/e26-d15/out.txt 2>&1
/data/benchrun.sh stand bash stand.sh pair alpha-e26-pgo alpha-e17b-pgo d15 $F > /data/tk048/e26-d15/outr.txt 2>&1
touch /data/tk048/e26-d15/done
EOS
systemctl reset-failed tk048-e26 2>/dev/null; systemd-run --quiet --unit=tk048-e26 --collect bash /data/tk048/e26-d15/run.sh && echo запущено'
