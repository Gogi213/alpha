#!/bin/bash
# TK-493, П-39: развёртка кода ПОИСКА на Steam Deck (запуск на ПК, Git Bash): git archive HEAD (LF, core.autocrlf=false) -> <DEST> на Deck + x_git_ok (sha256 по LF blob'ов коммита
# протокола, params и addendum П-39). Использование:  bash deploy_explore.sh [DEST]   (по умолчанию ~/xdl9/c2x/code). Рабочее дерево перед развёрткой должно быть чистым по этим путям.
set -eu
WT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
DEST=${1:-/home/deck/xdl9/c2x/code}
SSH="ssh -i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=20 deck@192.168.1.49"
cd "$WT"
for f in docs/research/P-39-convergence-explore-2026-11-07.md docs/research/P-39-params-2026-11-07.csv $(ls docs/research/P-39-params-calib-*.csv 2>/dev/null); do
  [ -z "$(git status --porcelain -- "$f")" ] || { echo "ОТКАЗ: $f изменён и не закоммичен"; exit 2; }
done
COMMIT=$(git rev-parse HEAD)
$SSH "rm -rf '$DEST.new' && mkdir -p '$DEST.new'"
git -c core.autocrlf=false archive --format=tar HEAD tools/compute/statarb docs/research/P-38-calib-gate-2026-11-07 docs/research/P-38-params-2026-11-07.csv docs/research/P-39-convergence-explore-2026-11-07.md \
  docs/research/P-39-params-2026-11-07.csv $(git ls-files 'docs/research/P-39-params-calib-*.csv') | $SSH "tar -x -C '$DEST.new'"
{
  printf '{"commit": "%s", "files": {' "$COMMIT"
  first=1
  for f in docs/research/P-39-convergence-explore-2026-11-07.md docs/research/P-39-params-2026-11-07.csv $(git ls-files 'docs/research/P-39-params-calib-*.csv'); do
    [ $first -eq 1 ] || printf ', '
    first=0
    printf '"%s": "%s"' "$f" "$(git -c core.autocrlf=false show "HEAD:$f" | tr -d '\r' | sha256sum | cut -d' ' -f1)"
  done
  printf '}}\n'
} | $SSH "cat > '$DEST.new/x_git_ok'"
$SSH "rm -rf '$DEST.old'; [ -d '$DEST' ] && mv '$DEST' '$DEST.old'; mv '$DEST.new' '$DEST'; rm -rf '$DEST.old'; cat '$DEST/x_git_ok'"
echo "развёрнуто: $COMMIT -> $DEST"
