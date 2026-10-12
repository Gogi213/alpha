#!/usr/bin/env bash
# TK-207: отправить на calc ровно сдаваемую голову: git archive HEAD (vendor/ отслеживается — внутри) + список файлов
# + полный хеш. Грязное дерево — отказ (сдаётся коммит, не рабочие правки). Дальше — calc-build.sh на calc.
#   tools/compute/gate/push-head.sh <каталог на calc, напр. /data/tk201> [рабочее дерево=.]
set -euo pipefail
W=${1:?каталог на calc}; cd "${2:-.}"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "дерево грязное (отслеживаемые правки) — сначала коммит"; exit 1; }
SHA=$(git rev-parse HEAD); T=$(mktemp -d)
git archive --format=tar.gz -o "$T/src.tgz" HEAD
git ls-files > "$T/files.lst"; echo "$SHA" > "$T/HEAD"
grep -q '^vendor/' "$T/files.lst" || { echo "в голове нет vendor/"; exit 1; }
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts)
ssh "${KEY[@]}" root@89.163.242.211 "mkdir -p $W"
scp -q "${KEY[@]}" "$T/src.tgz" "$T/files.lst" "$T/HEAD" root@89.163.242.211:$W/
rm -rf "$T"; echo "отправлено: $SHA -> calc:$W"
