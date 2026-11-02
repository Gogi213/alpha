#!/usr/bin/env bash
# TK-479: заявки на calc для ядра statarb. Не ждёт: печатает id; ждать — host:calc:/data/sched/rc/<id>.
#   statarb-calc.sh build <дерево>            релизная сборка (слот rbuild, 1 ядро резерва) → /data/tk0479/alpha-tk479
#   statarb-calc.sh check <дерево>            clippy -D warnings + test statarb (слот rbuild, 1 ядро резерва)
#   statarb-calc.sh bench [N символов] [ядра]  convert + bench (statarb-bench.sh), обычное prod-задание на <ядра> ядер
set -euo pipefail
MODE="${1:?build|bench}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@89.163.242.211
case "$MODE" in
build|check)
  TREE="${2:?дерево}"
  if [ "$MODE" = build ]; then
    STEPS='cargo build --release --target-dir $SLOT_TGT -j $JOBS 2>&1 | tail -3
install -D $SLOT_TGT/release/alpha /data/tk0479/alpha-tk479 && md5sum /data/tk0479/alpha-tk479'
  else
    STEPS='cargo clippy --release --target-dir $SLOT_TGT --all-targets -j $JOBS -- -D warnings 2>&1 | tail -5
cargo test --release --target-dir $SLOT_TGT -j $JOBS statarb 2>&1 | grep -E "^test result|FAILED|panicked|^error"'
  fi
  TMPD="${TEMP:-/tmp}"; command -v cygpath >/dev/null && TMPD="$(cygpath -u "$TMPD")"
  ARC="$TMPD/tk479-$$.tgz"; ARCR=/opt/rbuild/in/tk479-$$.tgz
  cd "$TREE"
  git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
  ssh "${KEY[@]}" "$HOST" "mkdir -p /opt/rbuild/in"
  scp -q "${KEY[@]}" "$ARC" "$HOST:$ARCR"; rm -f "$ARC"
  ssh "${KEY[@]}" "$HOST" "cat > /opt/rbuild/run.sh.$$ && mv /opt/rbuild/run.sh.$$ /opt/rbuild/run.sh" < tools/calc-build-run.sh
  ID=$(ssh "${KEY[@]}" "$HOST" "cat > /opt/rbuild/in/cmd-tk479-$$.sh <<'EOS'
export PATH=/opt/rbuild/cargo/bin:\$PATH RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo
set -eo pipefail
$STEPS
EOS
cd /opt/rbuild && SCHED_MANIFEST=0 python3 /data/sched/alsched.py submit --name build-TK-479-$MODE --max-runtime 25m --cls prod --kind build --cores 1 --mem 12 --prio 3 -- bash /opt/rbuild/run.sh $ARCR /opt/rbuild/in/cmd-tk479-$$.sh 1" | tail -1) ;;
bench)
  N="${2:-50}"; TH="${3:-8}"
  scp -q "${KEY[@]}" "$(dirname "$0")/statarb-bench.sh" "$HOST:/data/tk0479/statarb-bench.sh"
  ID=$(ssh "${KEY[@]}" "$HOST" "cd /data/sched && SCHED_MANIFEST=0 python3 /data/sched/alsched.py submit --name tk479-bench --max-runtime 30m --est 20m --cls prod --kind work --cores $TH --mem 24 --reason 'TK-479: замер пропускной способности ядра' --adhoc 'TK-479: разовый замер скорости' -- bash /data/tk0479/statarb-bench.sh /data/tk0479/alpha-tk479 $N $TH" | tail -1) ;;
*) echo "build|bench"; exit 2 ;;
esac
echo "alsched id $ID — wait_for: host:calc:/data/sched/rc/$ID (лог /data/sched/logs/$ID.log)"
