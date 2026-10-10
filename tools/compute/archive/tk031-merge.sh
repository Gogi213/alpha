#!/bin/bash
# TK-031 (в): слияние 65 (epochs/e-<м>/b5) и 11 монет (epochs11/e-<м>/b5) -> merged/e-<м>/b5; строки 65 — префикс файла байт в байт
export HOME=/home/deck; A=$HOME/alpha; O=$A/tk031; M=$A/merged; mkdir -p $M
: > $O/merge-summary.txt
for m in ${1:-jan feb mar apr may jun jul aug}; do
  rm -rf $M/e-$m; mkdir -p $M/e-$m
  A65=$A/epochs/e-$m/b5; A11=$A/epochs11/e-$m/b5; D=$M/e-$m/b5
  cp -a --reflink=auto $A65 $D 2>/dev/null || cp -a $A65 $D
  nf=0; nm=0; nn=0; hdr=0
  while read -r f; do
    nf=$((nf+1))
    if [ -e $D/$f ]; then
      cmp -s <(grep '^#' $D/$f) <(grep '^#' $A11/$f) || hdr=$((hdr+1))
      grep -v '^#' $A11/$f | tail -n +2 >> $D/$f; nm=$((nm+1))
    else mkdir -p $(dirname $D/$f); cp $A11/$f $D/$f; nn=$((nn+1)); fi
  done < <(cd $A11; find . -type f ! -name '*.log' | sed 's#^\./##' | LC_ALL=C sort)
  bad=0; tot=0
  while read -r f; do tot=$((tot+1)); s=$(stat -c%s $A65/$f); cmp -s -n $s $A65/$f $D/$f || bad=$((bad+1)); done < <(cd $A65; find . -type f ! -name '*.log' | sed 's#^\./##')
  echo "$m files11=$nf appended=$nm new=$nn header-diff=$hdr checked65=$tot prefix-diff=$bad" >> $O/merge-summary.txt
done
date > $O/MERGE_DONE
