#!/usr/bin/env bash
# TK-029: неделя августа сутками параллельно на ЛОКАЛЬНЫХ данных (stage ~/alpha/tk029/stage/root, без сети).
# tk029-local.sh <тег> <полосы через запятую> <сутки через запятую>. Гейт: sha без '#'-строк против wk2/ref (эталон прошлой недели).
set -uo pipefail
TAG="${1:?}"; LANES="${2:?}"; DAYS="${3:?}"
A="$HOME/alpha"; S="$A/tk029/stage/root"; H="$A/tk029/stageL"; O="$A/tk029/$TAG"; E="$A/epochs/e-augbench"
mkdir -p "$O/ref" "$H/study" "$H/b5" "$H/carry"; rm -f "$O/DONE" "$O/summary.txt"
cp "$A"/tk028/wk2/ref/body-*.sha "$O/ref/"
ln -sfn "$A/bin" "$H/bin"; ln -sfn "$H/carry" "$H/root"
rm -f "$H"/carry/*.binlog
for f in "$S"/*.binlog; do n=$(basename "$f"); [ -L "$A/epochs/e-aug/root/$n" -o -e "$A/epochs/e-aug/root/$n" ] && ln -sf "$f" "$H/carry/$n"; done
cp -L "$A"/epochs/e-aug/root/verify-*.status "$A"/epochs/e-aug/root/session.json "$A"/epochs/e-aug/root/instruments.csv "$H/carry/" 2>/dev/null
for e in "$E"/study/*; do n=$(basename "$e"); case "$n" in root-2026-08-*) ;; *) ln -sfn "$(readlink -f "$e")" "$H/study/$n";; esac; done
IFS=, read -ra DL <<< "$DAYS"
for d in "${DL[@]}"; do
  R="$H/study/root-$d"; rm -rf "$R"; mkdir -p "$R"
  for f in "$E/study/root-$d"/*; do n=$(basename "$f"); case "$n" in *.binlog) ln -sf "$S/$n" "$R/$n";; *) cp -L "$f" "$R/$n";; esac; done
done
python3 "$A/bin/p07-all-month.py" aug --days "$DAYS" --merge --bin "${BIN:-alpha-tk029-merge-v3}" > "$O/gen.out" 2>&1
sed -i "s#/epochs/e-aug/study/sigma240#/epochs/e-jul/study/sigma240#g" "$A"/tmp-p07/cells-by-day/jall-aug-2026-08-0[1-9].sh "$A"/tmp-p07/cells-by-day/jall-aug-2026-08-0[1-9].extra.txt
cd "$H" || exit 2
body() { find b5 -path "*$1*" -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done; }
runday() { local d="$1" m="$2"
  systemd-run --user --wait --collect -u "tk029-$TAG-$m-$d" -p MemoryAccounting=yes -p CPUAccounting=yes -p WorkingDirectory="$H" \
    -P bash -c "bash $A/tmp-p07/cells-by-day/jall-aug-$d.sh 2> $O/$m-$d.err" > "$O/$m-$d.props" 2>&1
  echo "rc=$?"; }
IFS=, read -ra LL <<< "$LANES"
for N in "${LL[@]}"; do
  rm -rf "$H"/b5/*
  t0=$(date +%s.%N)
  export -f runday; export TAG A H O
  printf '%s\n' "${DL[@]}" | xargs -P "$N" -I{} bash -c 'runday {} L'"$N"' > "$O/L'"$N"'-{}.rc"'
  t1=$(date +%s.%N)
  g=""; for d in "${DL[@]}"; do body "$d" > "$O/L$N-$d.body"; g="$g $d:diff=$(diff "$O/ref/body-$d.sha" "$O/L$N-$d.body" | wc -l)"; done
  echo "lanes=$N days=${#DL[@]} wall=$(python3 -c "print(round($t1-$t0,1))")$g" >> "$O/summary.txt"
  for d in "${DL[@]}"; do echo "  L$N $d $(cat "$O/L$N-$d.rc") $(grep -hE 'CPU time|Memory peak|Swap peak' "$O/L$N-$d.props" | tr '\n' ' ')" >> "$O/summary.txt"; done
done
date -Is > "$O/DONE"
