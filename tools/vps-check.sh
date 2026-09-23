#!/usr/bin/env bash
# Сборка и проверки рабочего дерева на VPS (13.140.29.171) — вторая дорожка работ, пока на этой машине идёт
# своя сборка (владелец 24.09: «одна сборка здесь, вторая на VPS»; две сборки на одной машине — нельзя).
#   tools/vps-check.sh <рабочее дерево> [test|clippy|fmt|build|all] [фильтр тестов]
# Переносит отслеживаемые и новые файлы дерева (без target*/data/) архивом с сохранением времени изменения —
# неизменённые файлы cargo не пересобирает; сборка — в отдельном target-wave2, не в каталоге сборок для дека.
set -euo pipefail
TREE="${1:?рабочее дерево}"
WHAT="${2:-all}"
FILTER="${3:-}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o ServerAliveInterval=30)
HOST=root@13.140.29.171
SRC=/opt/alpha-compute/wave2-src
TGT=/opt/alpha-compute/target-wave2
TMPD="${TEMP:-/tmp}"; command -v cygpath >/dev/null && TMPD="$(cygpath -u "$TMPD")"
ARC="$TMPD/wave2-$$.tgz"
cd "$TREE"
git ls-files -z --cached --others --exclude-standard | tar --force-local --null -T - -czf "$ARC"
scp -q "${KEY[@]}" "$ARC" "$HOST:/opt/alpha-compute/wave2.tgz"
rm -f "$ARC"
case "$WHAT" in
  test)   CMD="cargo test --release --target-dir $TGT -j 3 $FILTER 2>&1 | tail -25" ;;
  clippy) CMD="cargo clippy --release --target-dir $TGT --all-targets -j 3 -- -D warnings 2>&1 | tail -25" ;;
  fmt)    CMD="cargo fmt --check 2>&1 | tail -25" ;;
  build)  CMD="cargo build --release --target-dir $TGT -j 3 2>&1 | tail -5" ;;
  all)    CMD="cargo fmt --check 2>&1 | tail -10 && cargo clippy --release --target-dir $TGT --all-targets -j 3 -- -D warnings 2>&1 | tail -15 && cargo test --release --target-dir $TGT -j 3 2>&1 | tail -8" ;;
  *) echo "неизвестно: $WHAT"; exit 2 ;;
esac
# shellcheck disable=SC2029
ssh "${KEY[@]}" "$HOST" "set -o pipefail; rm -rf $SRC && mkdir -p $SRC && tar -xzf /opt/alpha-compute/wave2.tgz -C $SRC \
  && cd $SRC && export PATH=\$HOME/.cargo/bin:\$PATH && nice -n 5 bash -c '$CMD'"
