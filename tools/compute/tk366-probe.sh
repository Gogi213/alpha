#!/bin/bash
# TK-366: проба замера (3 фьюч-монеты × 3 суток разного размера + 1 спот × 2 суток) — размер бинлога/сырья, время импорта.
export BIN=/opt/alpha-compute/bin/alpha-tk312-pgo ROOT=/srv/alpha-sda/tk366/probe INSTR=/srv/alpha-sda/tk366/instruments.csv
export LOG=/srv/alpha-sda/tk366/probe/import.tsv TMP=/srv/alpha-sda/tk366/tmp JOBS=2
mkdir -p "$TMP" "$ROOT"
bash /srv/alpha-sda/tk366/tk366-run.sh fut /srv/alpha-sda/tk366/probe-fut.txt
bash /srv/alpha-sda/tk366/tk366-run.sh spot /srv/alpha-sda/tk366/probe-spot.txt
touch "$ROOT/done"
