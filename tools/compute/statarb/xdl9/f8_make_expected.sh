#!/usr/bin/env bash
# TK-493 w5 F (D3, D19): пересобрать f8_expected.env из ЗАКОММИЧЕННОГО содержимого (git show HEAD:...), если после коммита изменились params, протокол П-37 или список единиц
# (например, после предсчётной ревизии). Работает на ПК в рабочем дереве git; sha256 берётся от блоба HEAD, а не рабочей копии (autocrlf).
#
#   bash f8_make_expected.sh <sha256 боевого бинаря (PGO)> [F8_NEED_GB]
#   затем: git add tools/compute/statarb/xdl9/f8_expected.env && git commit ...; bash f8_run.sh --git-token; скопировать f8_git_ok, f8_expected.env, params и протокол на Deck.
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
BIN_SHA="${1:?нужен sha256 боевого бинаря (PGO-сборка финального исходника, D3)}"
NEED="${2:-8}"
PARAMS_REL="docs/research/P-37-params-2026-11-06.csv"
PROTO_REL="docs/research/P-37-statarb-xdl9-probe.md"
UNITS_REL="$(git -C "$HERE" ls-files --full-name -- f8_units.tsv | head -1)"
blob() { git -C "$REPO" show "HEAD:$1" | sha256sum | cut -d' ' -f1; }
for f in "$PARAMS_REL" "$PROTO_REL" "$UNITS_REL"; do
  [ -z "$(git -C "$REPO" status --porcelain -- "$f")" ] || { echo "ОТКАЗ: $f изменён относительно HEAD - сначала коммит"; exit 1; }
done
{
  echo "F8_BIN_SHA256=$BIN_SHA"
  echo "F8_PARAMS_REL=$PARAMS_REL"
  echo "F8_PARAMS_SHA256=$(blob "$PARAMS_REL")"
  echo "F8_PROTO_REL=$PROTO_REL"
  echo "F8_PROTO_SHA256=$(blob "$PROTO_REL")"
  echo "F8_UNITS_SHA256=$(blob "$UNITS_REL")"
  echo "F8_NEED_GB=$NEED"
} > "$HERE/f8_expected.env"
cat "$HERE/f8_expected.env"
