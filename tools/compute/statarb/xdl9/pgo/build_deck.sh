#!/usr/bin/env bash
# TK-493 (В-100, 07.11): сборка ядра на Steam Deck вместо VPS Софии. Запускать с ПК владельца (в нём нет cargo — только git archive и ssh).
#   build_deck.sh <base|plain|gen|use|merge> [rev]     rev по умолчанию HEAD; бинарь → ~/xdl9/pgo-deck/bin/statarb-xdl9-<sha8>-<шаг>
# Исходники = закоммиченное дерево rev (git archive), поэтому сборка воспроизводима по sha; рецепт — build_pgo_vps.sh (тот же файл, JOBS=8, без замка VPS).
# Внимание: у Deck свой rustc (1.94.0), у VPS был 1.93.1 — профиль PGO (gen → merge → use) собирать ТОЛЬКО одним и тем же компилятором.
set -uo pipefail
STEP=${1:?base|plain|gen|use|merge}; REV=${2:-HEAD}
HERE=$(cd "$(dirname "$0")" && pwd)
SHA=$(git -C "$HERE" rev-parse --short=8 "$REV") || exit 2
KEY=${DECK_KEY:-$HOME/.ssh/id_rsa}; KH=${DECK_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}; DECK=${DECK:-deck@192.168.1.49}
SSH="ssh -i $KEY -o UserKnownHostsFile=$KH -o ConnectTimeout=15 $DECK"
W='$HOME/xdl9/pgo-deck'
T0=$(date +%s)
$SSH "mkdir -p $W && rm -rf $W/src-$SHA && mkdir -p $W/src-$SHA" || exit 3
git -C "$(git -C "$HERE" rev-parse --show-toplevel)" archive "$REV" | $SSH "tar -x -C $W/src-$SHA" || exit 3
scp -q -i "$KEY" -o UserKnownHostsFile="$KH" "$HERE/build_pgo_vps.sh" "$DECK:xdl9/pgo-deck/build_pgo_vps.sh" || exit 3
$SSH "W=$W SRC=$W/src-$SHA NAME=statarb-xdl9-$SHA ALPHA_COMMIT=$SHA LOCK= JOBS=8 NICE=${NICE:-10} bash $W/build_pgo_vps.sh $STEP"
RC=$?
echo "build_deck: $STEP $SHA rc=$RC за $(( $(date +%s) - T0 )) с"
exit $RC
