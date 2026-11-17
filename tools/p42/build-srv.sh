#!/usr/bin/env bash
# Сборка бинаря alpha на srv через раннер /opt/rbuild (замок сборок, тёплый target-srv, nice 19, 2 ядра) → $BIN_OUT.
# Использование: bash tools/p42/build-srv.sh <BIN_OUT на srv> [test <фильтр>]
set -euo pipefail
BIN_OUT="${1:?BIN_OUT на srv}"; MODE="${2:-build}"; FILTER="${3:-}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
T="${TEMP:-/tmp}"; command -v cygpath >/dev/null && T="$(cygpath -u "$T")"
ARC="$T/p42-build-$$.tgz"; ARCR=/opt/rbuild/in/p42-$$.tgz; CMDF=/opt/rbuild/in/p42cmd-$$.sh
git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
ssh "${K[@]}" "$H" "mkdir -p /opt/rbuild/in"
scp -q "${K[@]}" "$ARC" "$H:$ARCR"; rm -f "$ARC"
case "$MODE" in
  build) CMD="cargo build --release --bin alpha --target-dir \$SLOT_TGT -j \$JOBS > /tmp/p42b-$$.log 2>&1; RC=\$?; grep -A14 -E '^(error|warning)' /tmp/p42b-$$.log | head -80; tail -2 /tmp/p42b-$$.log; [ \$RC -eq 0 ] && install -D \$SLOT_TGT/release/alpha $BIN_OUT && md5sum $BIN_OUT" ;;
  test)  CMD="cargo test --release --target-dir \$SLOT_TGT -j \$JOBS $FILTER 2>&1 | grep -E '^test |test result|FAILED|panicked|^error|^warning' | tail -60" ;;
  all)   CMD="cargo fmt --check 2>&1 | tail -10 && cargo clippy --release --target-dir \$SLOT_TGT -j \$JOBS --all-targets -- -D warnings 2>&1 | tail -15 && cargo test --release --target-dir \$SLOT_TGT -j \$JOBS $FILTER 2>&1 | grep -E 'test result|FAILED|panicked|^error' | tail -20" ;;
  *) echo "mode: build|test|all"; exit 2 ;;
esac
ssh "${K[@]}" "$H" "cat > $CMDF" <<EOS
export PATH=/opt/rbuild/cargo/bin:\$PATH RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo
set -o pipefail
$CMD
EOS
ssh "${K[@]}" "$H" "SRV_CORES=${SRV_CORES:-2} bash /opt/rbuild/run-srv.sh $ARCR $CMDF"
