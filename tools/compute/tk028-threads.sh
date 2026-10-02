#!/usr/bin/env bash
# TK-028: отрезок 1 суток при разных --threads, по очереди (порядок в списке), каждый юнитом; гейт: sha содержимого без '#'-строк
# против эталона manifest.body.sha (REF). tk028-threads.sh <СУТКИ> <тег> <REF-manifest.body.sha> <n1> <n2> ...
set -uo pipefail
D="${1:?сутки}"; TAG="${2:?тег}"; REF="${3:?эталон}"; shift 3
A="$HOME/alpha"; H="$A/epochs/e-augbench"; O="$A/tk028/$TAG"; mkdir -p "$O"; rm -f "$O/DONE" "$O/summary.txt"
SEG="$A/tk028/b2/seg1.sh"
cd "$H" || exit 2
for n in "$@"; do
  sed -E "s/ --threads [0-9]+ / --threads $n /" "$SEG" > "$O/seg1-t$n.sh"
  t0=$(date +%s.%N)
  systemd-run --user --wait --collect -u "tk028-$TAG-t$n" -p MemoryAccounting=yes -p CPUAccounting=yes \
    -p WorkingDirectory="$H" -p StandardOutput=file:"$O/t$n.out" -p StandardError=file:"$O/t$n.err" \
    -P bash "$O/seg1-t$n.sh" > "$O/t$n.props" 2>&1
  rc=$?; t1=$(date +%s.%N)
  find b5 -path "*$D*" -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > "$O/body-t$n.sha"
  echo "t$n rc=$rc wall=$(python3 -c "print(round($t1-$t0,1))") diff_vs_ref=$(diff "$REF" "$O/body-t$n.sha" | wc -l) $(grep -hE 'CPU time|Memory peak' "$O/t$n.props" | tr '\n' ' ')" >> "$O/summary.txt"
done
date -Is > "$O/DONE"
