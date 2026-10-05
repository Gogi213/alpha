#!/usr/bin/env bash
# Очередь слияний кодовых веток в рабочую ветку (TK-057). Правило: кодовая задача живёт в своём worktree/ветке;
# в рабочую ветку код попадает только так, по одному (замок), fast-forward, после зелёного vps-check all.
#   bash tools/team/merge-queue.sh <ветка> [--check-only]
# Запускать из основного дерева (оно на рабочей ветке). Основное дерево по src/ tools/ vendor/ Cargo.* — чистое.
set -euo pipefail
BR="${1:?ветка}"; ONLY="${2:-}"
MAIN="$(git rev-parse --show-toplevel)"; cd "$MAIN"
WORK="$(git rev-parse --abbrev-ref HEAD)"
LOCK="$(git rev-parse --git-common-dir)/merge-queue.lock"
until mkdir "$LOCK" 2>/dev/null; do echo "очередь занята ($(cat "$LOCK/who" 2>/dev/null)), жду 30 с"; sleep 30; done
echo "$BR $(date +%H:%M:%S)" > "$LOCK/who"; trap 'rm -rf "$LOCK"' EXIT
dirty="$(git status --porcelain --untracked-files=all -- src tools vendor Cargo.toml Cargo.lock)"
[ -z "$dirty" ] || { echo "ОТКАЗ: в основном дереве незакоммиченное по src/tools/vendor/Cargo:"; echo "$dirty"; exit 2; }
git rev-parse -q --verify "$BR" >/dev/null || { echo "нет ветки $BR"; exit 2; }
WT="$(git worktree list --porcelain | awk -v b="refs/heads/$BR" '/^worktree /{p=substr($0,10)} $0=="branch " b{print p}')"
[ -n "$WT" ] || { WT="$MAIN/../alpha-q-$BR"; git worktree add -q "$WT" "$BR"; }
[ -z "$(git -C "$WT" status --porcelain --untracked-files=no)" ] || { echo "ОТКАЗ: в worktree ветки $WT есть незакоммиченное"; exit 2; }
while :; do
  HEAD0="$(git rev-parse "$WORK")"
  if ! git merge-base --is-ancestor "$HEAD0" "$BR"; then
    if ! git -C "$WT" rebase "$HEAD0" >/dev/null 2>&1; then
      git -C "$WT" rebase --abort 2>/dev/null || true
      echo "rebase не прошёл (ветка со слияниями или конфликт) — пробую merge $WORK в ветку"
      git -C "$WT" merge --no-edit "$HEAD0" >/dev/null 2>&1 || { git -C "$WT" merge --abort 2>/dev/null || true; echo "ОТКАЗ: конфликт — разреши в $WT (git merge $WORK) и поставь в очередь снова"; exit 3; }
    fi
  fi
  echo "vps-check all: $BR @ $(git rev-parse --short "$BR") поверх $WORK @ ${HEAD0:0:8}"
  VPS_TAG="${VPS_TAG:-q}" bash "$MAIN/tools/vps-check.sh" "$WT" all | tee "$LOCK/check.log"
  grep -q "FAILED\|^error\|panicked" "$LOCK/check.log" && { echo "ОТКАЗ: vps-check красный"; exit 4; }
  grep -q "^test result: ok" "$LOCK/check.log" || { echo "ОТКАЗ: нет строки test result: ok"; exit 4; }
  [ "$ONLY" = "--check-only" ] && { echo "check-only: зелёный, слияние не делаю"; exit 0; }
  if [ "$(git rev-parse "$WORK")" = "$HEAD0" ]; then
    git merge --ff-only "$BR" && { echo "СЛИТО: $WORK = $(git rev-parse --short HEAD) (ветка $BR)"; exit 0; }
    echo "ff не удался"; exit 5
  fi
  echo "рабочая ветка ушла вперёд за время проверки — повтор"
done
