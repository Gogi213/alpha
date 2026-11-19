#!/usr/bin/env bash
# Сборка бинаря alpha на srv через раннер /opt/rbuild (очередь с приоритетом, тёплый target-srv, nice 19, 2 ядра) → $BIN_OUT.
# Использование: [RBUILD_PRIO=0|1|2] [BIN_NAME=alpha-feat] bash tools/p42/build-srv.sh <BIN_OUT на srv> [test <фильтр>]   (BIN_NAME — бинарь из src/bin, по умолчанию alpha)
# TK-614: бинарь ставится по содержимому — $BIN_OUT.<md5, 8 символов> + атомарная ссылка $BIN_OUT (tools/rbuild-install.sh; старый запрос
# из очереди не затирает ссылку, поставленную новым); приоритет по умолчанию: build — 0, test — 1, all — 2; отмена — `bash /opt/rbuild/cancel.sh <id>`
# на srv (id — первая строка вывода раннера); раннер выкладывается версией /opt/rbuild/run-srv.<md5>.sh.
set -euo pipefail
BIN_OUT="${1:?BIN_OUT на srv}"; MODE="${2:-build}"; FILTER="${3:-}"; BIN_NAME="${BIN_NAME:-alpha}"
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30)
H=root@5.231.25.224
RB="${RBUILD_DIR:-/opt/rbuild}"
cd "$(dirname "$0")/../.."
T="${TEMP:-/tmp}"; command -v cygpath >/dev/null && T="$(cygpath -u "$T")"
ARC="$T/p42-build-$$.tgz"; ARCR=$RB/in/p42-$$.tgz; CMDF=$RB/in/p42cmd-$$.sh
RSH=tools/srv-build-run.sh; RUNR=$RB/run-srv.$(md5sum < "$RSH" | cut -c1-8).sh
HELPER=
git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
ssh "${K[@]}" "$H" "mkdir -p $RB/in && { [ -s $RUNR ] || { cat > $RUNR.$$ && mv $RUNR.$$ $RUNR; }; }" < "$RSH"
scp -q "${K[@]}" "$ARC" "$H:$ARCR"; rm -f "$ARC"
case "$MODE" in
  build) PRIO=0; HELPER="$(cat tools/rbuild-install.sh)"
         CMD="cargo build --release --bin $BIN_NAME --target-dir \$SLOT_TGT -j \$JOBS > /tmp/p42b-$$.log 2>&1; RC=\$?; grep -A14 -E '^(error|warning)' /tmp/p42b-$$.log | head -80; tail -2 /tmp/p42b-$$.log; [ \$RC -eq 0 ] && rb_install \$SLOT_TGT/release/$BIN_NAME $BIN_OUT" ;;
  test)  PRIO=1; CMD="cargo test --release --target-dir \$SLOT_TGT -j \$JOBS $FILTER 2>&1 | grep -E '^test |test result|FAILED|panicked|^error|^warning' | tail -60" ;;
  all)   PRIO=2; CMD="cargo fmt --check 2>&1 | tail -10 && cargo clippy --release --target-dir \$SLOT_TGT -j \$JOBS --all-targets -- -D warnings 2>&1 | tail -15 && cargo test --release --target-dir \$SLOT_TGT -j \$JOBS $FILTER 2>&1 | grep -E 'test result|FAILED|panicked|^error' | tail -20" ;;
  full)  PRIO=2; HELPER="$(cat tools/rbuild-install.sh)"
         CMD="cargo fmt --check 2>&1 | tail -10; cargo build --release --bin alpha --target-dir \$SLOT_TGT -j \$JOBS > /tmp/p42b-$$.log 2>&1; RC=\$?; grep -A14 -E '^(error|warning)' /tmp/p42b-$$.log | head -60; tail -2 /tmp/p42b-$$.log; [ \$RC -eq 0 ] && rb_install \$SLOT_TGT/release/alpha $BIN_OUT; cargo test --release --target-dir \$SLOT_TGT -j \$JOBS $FILTER 2>&1 | grep -E 'test result|FAILED|panicked|^error' | tail -20; cargo clippy --release --target-dir \$SLOT_TGT -j \$JOBS --all-targets -- -D warnings 2>&1 | tail -15; exit \$RC" ;;
  *) echo "mode: build|test|all|full"; exit 2 ;;
esac
ssh "${K[@]}" "$H" "cat > $CMDF" <<EOS
export PATH=$RB/cargo/bin:\$PATH RUSTUP_HOME=$RB/rustup CARGO_HOME=$RB/cargo
set -o pipefail
$HELPER
$CMD
EOS
ssh "${K[@]}" "$H" "RBUILD_ID=$$ RBUILD_PRIO=${RBUILD_PRIO:-$PRIO} RBUILD_DIR=$RB SRV_CORES=${SRV_CORES:-2} bash $RUNR $ARCR $CMDF"
