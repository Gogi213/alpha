#!/usr/bin/env bash
# tk051-build.sh <дерево> <имя бинарника> [features] [RUSTFLAGS-добавка]: сборка на VPS (v3, свой target-tk051) -> /opt/alpha-compute/bin/<имя>
set -euo pipefail
TREE="${1:?дерево}"; NAME="${2:?имя}"; FEAT="${3:-}"; XFL="${4:-}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@13.140.29.171; SRC=/opt/alpha-compute/wave2-srctk051
TMPD="${TEMP:-/tmp}"; command -v cygpath >/dev/null && TMPD="$(cygpath -u "$TMPD")"; ARC="$TMPD/tk051-$$.tgz"
cd "$TREE"; git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
scp -q "${KEY[@]}" "$ARC" "$HOST:/opt/alpha-compute/tk051.tgz"; rm -f "$ARC"
F=""; [ -n "$FEAT" ] && F="--features $FEAT"
ssh "${KEY[@]}" "$HOST" "export HOME=/root CARGO_TARGET_DIR=/opt/alpha-compute/target-tk051 RUSTFLAGS='-C target-cpu=x86-64-v3 $XFL'; rm -rf $SRC && mkdir -p $SRC && tar -xzf /opt/alpha-compute/tk051.tgz -C $SRC && cd $SRC \
 && flock -w 7200 /opt/alpha-compute/.build.lock nice -n 5 /root/.cargo/bin/cargo build --release -j 3 --bin alpha $F 2>&1 | tail -4 \
 && cp \$CARGO_TARGET_DIR/release/alpha /opt/alpha-compute/bin/$NAME && md5sum /opt/alpha-compute/bin/$NAME"
