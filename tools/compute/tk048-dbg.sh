#!/usr/bin/env bash
# TK-048: «прочее» строки круга по строкам исходника — сборка debug=1 (код не меняется, исходники e20) и perf на стенде d15.
#   build — запустить юнит сборки на VPS (alpha-e21-dbg, done: /opt/alpha-compute/tk048-dbg.done)
#   run   — выложить на calc и запустить юнит tk048-dbg (perf record -F 499 по стенду d15 через benchrun; done: /data/tk048/dbg-d15/done)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
n=alpha-e21-dbg; B=/opt/alpha-compute/bin
case ${1:?build|run} in
build)
ssh "${K[@]}" $V "cat > /opt/alpha-compute/tk048-dbg.sh <<'EOS'
export HOME=/root CARGO_TARGET_DIR=/opt/alpha-compute/target-tk048-dbg RUSTFLAGS='-C target-cpu=x86-64-v3 ' CARGO_PROFILE_RELEASE_DEBUG=1
SRC=/opt/alpha-compute/tk048-dbg-src
rm -f /opt/alpha-compute/tk048-dbg.done /opt/alpha-compute/tk048-dbg.fail
rm -rf \$SRC && mkdir -p \$SRC && tar -xzf /opt/alpha-compute/e20.tgz -C \$SRC && cd \$SRC || { touch /opt/alpha-compute/tk048-dbg.fail; exit 1; }
if flock -w 14400 /opt/alpha-compute/.build.lock /opt/alpha-compute/sweep.sh run \$CARGO_TARGET_DIR bash -c 'nice -n 5 /root/.cargo/bin/cargo build --release -j 3 --bin alpha && cp \$CARGO_TARGET_DIR/release/alpha $B/$n' > /opt/alpha-compute/tk048-dbg.log 2>&1; then touch /opt/alpha-compute/tk048-dbg.done; else touch /opt/alpha-compute/tk048-dbg.fail; fi
EOS
systemctl reset-failed tk048-dbg-build 2>/dev/null; systemd-run --quiet --unit tk048-dbg-build --collect bash /opt/alpha-compute/tk048-dbg.sh && echo юнит сборки запущен" ;;
run)
ssh "${K[@]}" $V "test -e /opt/alpha-compute/tk048-dbg.done"
m=$(ssh "${K[@]}" $V "md5sum < $B/$n" | cut -d' ' -f1)
ssh "${K[@]}" $V "cat $B/$n" | ssh "${K[@]}" $C "cat > $B/$n.new && chmod +x $B/$n.new"
m2=$(ssh "${K[@]}" $C "md5sum < $B/$n.new" | cut -d' ' -f1)
[ "$m" = "$m2" ] || { echo "md5 не сошёлся"; exit 1; }
ssh "${K[@]}" $C "mv $B/$n.new $B/$n; mkdir -p /data/tk048/dbg-d15; rm -f /data/tk048/dbg-d15/done
cat > /data/tk048/dbg-d15/run.sh <<'EOS'
#!/bin/bash
D=/data/tk048/dbg-d15; cd /data/tk051
/data/benchrun.sh stand perf record -F 499 -o \$D/perf.data -- bash stand.sh alpha-e21-dbg d15 ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 > \$D/out.txt 2>&1
perf report -i \$D/perf.data --no-children --sort sym --stdio 2>/dev/null | grep -v '^#' | grep -v '^\$' | head -60 > \$D/top-sym.txt
timeout 1500 perf report -i \$D/perf.data --no-children --sort srcline --stdio 2>/dev/null | grep -v '^#' | grep -v '^\$' | head -150 > \$D/top-line.txt
touch \$D/done
EOS
systemctl reset-failed tk048-dbg 2>/dev/null; systemd-run --quiet --unit tk048-dbg --collect bash /data/tk048/dbg-d15/run.sh && echo юнит tk048-dbg запущен" ;;
esac
