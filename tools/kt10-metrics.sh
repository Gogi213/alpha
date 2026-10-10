#!/usr/bin/env bash
# КТ-10 (TK-155): метрики §1(6) на ревизии. Использование: tools/kt10-metrics.sh [rev=HEAD]
# До/после: paste <(tools/kt10-metrics.sh c65d22d9) <(tools/kt10-metrics.sh HEAD)
set -u
rev=${1:-HEAD}
cd "$(git rev-parse --show-toplevel)"
sumlines() { git grep -c '' "$rev" -- "$@" | awk -F: '{s+=$NF} END{print s+0}'; }
echo "rev	$(git rev-parse --short "$rev")"
echo "rust_lines	$(sumlines 'src/*.rs')"
echo "tools_lines	$(sumlines 'tools/*.sh' 'tools/*.py' ':!tools/archive')"
echo "alpha_env_names	$(git grep -hoE 'ALPHA_[A-Z0-9_]+' "$rev" -- src tools ':!tools/archive' | sort -u | wc -l)"
echo "tools_compute_root_files	$(git ls-tree -r --name-only "$rev" tools/compute | awk -F/ "NF==3" | wc -l)"
echo "branches_local_tk	$(git branch --list 'tk*' | wc -l)"
echo "branches_local_all	$(git branch | wc -l)"
echo "archive_tags	$(git tag --list 'archive/*' | wc -l)"
