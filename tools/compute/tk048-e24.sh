#!/usr/bin/env bash
# TK-048: e24 = e22 + advance без dyn (SeenKind); пара d15 против e22, diff 0
#   build — git archive HEAD → VPS, юнит сборки alpha-e24 (done: /opt/alpha-compute/tk048-e24.done)
#   pair  — выложить на calc, стенд pair d15 (done: /data/tk048/e24-d15/done), результат в out.txt
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
n=alpha-e24; B=/opt/alpha-compute/bin
case ${1:?build|pair} in
build)
git archive --format=tar.gz HEAD | ssh "${K[@]}" $V "cat > /opt/alpha-compute/e24.tgz"
ssh "${K[@]}" $V "cat > /opt/alpha-compute/tk048-e24.sh <<'EOS'
export HOME=/root CARGO_TARGET_DIR=/opt/alpha-compute/target-tk048-e24 RUSTFLAGS='-C target-cpu=x86-64-v3 '
SRC=/opt/alpha-compute/tk048-e24-src
rm -f /opt/alpha-compute/tk048-e24.done /opt/alpha-compute/tk048-e24.fail
rm -rf \$SRC && mkdir -p \$SRC && tar -xzf /opt/alpha-compute/e24.tgz -C \$SRC && cd \$SRC || { touch /opt/alpha-compute/tk048-e24.fail; exit 1; }
if flock -w 14400 /opt/alpha-compute/.build.lock /opt/alpha-compute/sweep.sh run \$CARGO_TARGET_DIR bash -c 'nice -n 5 /root/.cargo/bin/cargo build --release -j 3 --bin alpha && cp \$CARGO_TARGET_DIR/release/alpha $B/$n' > /opt/alpha-compute/tk048-e24.log 2>&1; then touch /opt/alpha-compute/tk048-e24.done; else touch /opt/alpha-compute/tk048-e24.fail; fi
EOS
systemctl reset-failed tk048-e24-build 2>/dev/null; systemd-run --quiet --unit tk048-e24-build --collect bash /opt/alpha-compute/tk048-e24.sh && echo юнит сборки запущен" ;;
pair)
ssh "${K[@]}" $V "test -e /opt/alpha-compute/tk048-e24.done"
m=$(ssh "${K[@]}" $V "md5sum < $B/$n" | cut -d' ' -f1)
ssh "${K[@]}" $V "cat $B/$n" | ssh "${K[@]}" $C "cat > $B/$n.new && chmod +x $B/$n.new"
m2=$(ssh "${K[@]}" $C "md5sum < $B/$n.new" | cut -d' ' -f1)
[ "$m" = "$m2" ] || { echo "md5 не сошёлся"; exit 1; }
ssh "${K[@]}" $C "mv $B/$n.new $B/$n; mkdir -p /data/tk048/e24-d15; rm -f /data/tk048/e24-d15/done
cat > /data/tk048/e24-d15/run.sh <<'EOS'
#!/bin/bash
cd /data/tk051
/data/benchrun.sh stand bash stand.sh pair alpha-e22 alpha-e24 d15 ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 > /data/tk048/e24-d15/out.txt 2>&1
touch /data/tk048/e24-d15/done
EOS
systemctl reset-failed tk048-e24 2>/dev/null; systemd-run --quiet --unit tk048-e24 --collect bash /data/tk048/e24-d15/run.sh && echo юнит tk048-e24 запущен" ;;
esac
