#!/bin/bash
# tk048-relink.sh: ссылки study/root-<D>/* суток на sda -> копия /alpha-sda/tk048 (дополняет sda-map.tsv для --revert). Идемпотентно.
M=/data/tk048/sda-map.tsv; declare -A NEW OLD
while IFS=$'\t' read -r link old new; do NEW[$old]=$new; done < <(awk -F'\t' '!s[$2]++' $M)
n=0
for mon in jan feb; do S=/data/tk046/$mon/home/alpha/epochs/e-$mon/study
  for l in $S/root-*/*.binlog; do [ -L "$l" ] || continue; t=$(readlink "$l"); new=${NEW[$t]}; [ -n "$new" ] || continue
    echo -e "$l\t$t\t$new" >> $M.study; ln -sfn "$new" "$l"; n=$((n+1)); done; done
cat $M.study >> $M; rm $M.study; echo relinked $n
