#!/usr/bin/env bash
# TK-263 п.1 (В-21): A/B по числу слотов NX на мерке bs-launch.sh, один проход на значение, всё в одном окне замка:
#   /data/benchrun.sh wave bash tk263-nx.sh   (env D=<каталог среза>, BS=<bs-launch.sh>, STEPS="метка:NX …")
# Умолчание: A1 (NX 12 — форма), n6, n4, n2, A2 (NX 12 — повтор для шума/дрейфа). Диф выходов — против ref.md5 каталога (база TK-262).
set -u
D=${D:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}
for st in ${STEPS:-A1:12 n6:6 n4:4 n2:2 A2:12}; do
  NX=${st#*:} bash "$BS" pass "$D" "${st%%:*}" > "$D.${st%%:*}.out" 2>&1
done
touch "$D.done"
