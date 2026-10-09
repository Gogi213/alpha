#!/bin/bash
# TK-068: по одной шапке «# lob bounce-grid: …» на группу (1-й месяц, где есть, 1-е сутки, 1-й набор) -> /data/registry/hdr-tk040.tsv
R=/data/tk048/tk040-b14; O=/data/registry/hdr-tk040.tsv; : > $O
for g in $(ls $R/jan/b5 | grep -v '^\.'); do
  f=$(ls -d $R/jan/b5/$g/*/*/forms.csv 2>/dev/null | head -1)
  [ -n "$f" ] && printf '%s\t%s\n' "$g" "$(head -1 "$f")" >> $O
done
touch /data/registry/hdr.done
