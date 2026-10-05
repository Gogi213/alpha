#!/usr/bin/env bash
# pgo-cycle.sh gen|finish <дерево> <имя> [RUSTFLAGS-добавка для обеих сборок, напр. "-C codegen-units=1 -C panic=abort"]
#   gen:    сборка VPS с -C profile-generate -> бинарник на сервер счёта -> юнит tk051-pgotrain-<имя> обучает на стенде
#           (ATOM 01-15 + все символы 01-01, связка b1); маркер /data/tk051/pgotrain-<имя>.done. После него — finish.
#   finish: profraw -> VPS, llvm-profdata merge, сборка с -C profile-use -> /opt/alpha-compute/bin/alpha-<имя>-pgo -> копия на сервер счёта.
# Дальше пара на стенде: stand.sh pair <базовый> alpha-<имя>-pgo d15|d01 ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1
set -euo pipefail
ST="${1:?gen|finish}"; TREE="${2:?дерево}"; N="${3:?имя}"; XFL="${4:-}"
D="$(cd "$(dirname "$0")" && pwd)"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
VPS=root@13.140.29.171; CALC=root@89.163.242.211; RAW=/data/tk051/pgo-raw-$N; PD=/opt/alpha-compute/pgo/$N
ship() { ssh "${K[@]}" $1 "cat $3" | ssh "${K[@]}" $2 "cat > $4 && chmod +x $4 && md5sum $4"; }
case "$ST" in
gen)
  bash "$D/tk051-build.sh" "$TREE" "alpha-$N-gen" "" "$XFL -C profile-generate=$RAW"
  ship $VPS $CALC /opt/alpha-compute/bin/alpha-$N-gen /opt/alpha-compute/bin/alpha-$N-gen
  ssh "${K[@]}" $CALC "rm -rf $RAW /data/tk051/pgotrain-$N.done; cat > /data/tk051/pgotrain-$N.sh <<'EOF'
#!/bin/bash
cd /data/tk051
for m in atom d01; do bash stand.sh alpha-$N-gen \$m ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 > pgotrain-$N-\$m.out 2>&1; done
touch /data/tk051/pgotrain-$N.done
EOF
systemd-run --quiet --unit=tk051-pgotrain-$N bash /data/tk051/pgotrain-$N.sh; echo обучение запущено"
  ;;
finish)
  ssh "${K[@]}" $CALC "test -e /data/tk051/pgotrain-$N.done && cat $RAW/*.profraw" | ssh "${K[@]}" $VPS "mkdir -p $PD && cat > $PD/a.profraw && \$(ls /root/.rustup/toolchains/*/lib/rustlib/*/bin/llvm-profdata) merge -o $PD/merged.profdata $PD/a.profraw && ls -l $PD"
  bash "$D/tk051-build.sh" "$TREE" "alpha-$N-pgo" "" "$XFL -C profile-use=$PD/merged.profdata -C llvm-args=-pgo-warn-missing-function"
  ship $VPS $CALC /opt/alpha-compute/bin/alpha-$N-pgo /opt/alpha-compute/bin/alpha-$N-pgo
  ;;
*) echo "gen|finish"; exit 2 ;;
esac
