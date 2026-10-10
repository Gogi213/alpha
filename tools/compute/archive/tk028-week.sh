#!/usr/bin/env bash
# TK-028: неделя августа сутками параллельно. tk028-week.sh <тег> <полосы через запятую> <сутки через запятую> <эталонные-последовательные сутки через запятую>
# Фаза 0: эталон — перечисленные сутки по одним, последовательно (wall суток целиком = отрезки 1+2+3 одним сценарием jall).
# Фаза N: все сутки, не более N одновременно (каждые сутки — юнит с CPU/памятью). Гейт: sha содержимого без '#'-строк;
# эталон суток = фаза 0, иначе первый встретившийся прогон (в сводке ref=serial|first); 2026-08-01 — tk028/b2.
set -uo pipefail
TAG="${1:?}"; LANES="${2:?}"; DAYS="${3:?}"; REFD="${4:-}"
A="$HOME/alpha"; H="$A/epochs/e-augbench"; O="$A/tk028/$TAG"; mkdir -p "$O/ref"; rm -f "$O/DONE" "$O/summary.txt"
cd "$H" || exit 2
IFS=, read -ra DL <<< "$DAYS"
python3 "$A/bin/p07-all-month.py" aug --days "$DAYS" > "$O/gen.out" 2>&1
# замер скорости: σ июля (sigma240 для e-aug нет — без файла bounce-grid молча выходит с кодом 1)
sed -i "s#/epochs/e-aug/study/sigma240#/epochs/e-jul/study/sigma240#g" "$A"/tmp-p07/cells-by-day/jall-aug-2026-08-0[1-9].sh
body() { find b5 -path "*$1*" -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done; }
runday() { # день метка
  local d="$1" m="$2"
  systemd-run --user --wait --collect -u "tk028-$TAG-$m-$d" -p MemoryAccounting=yes -p CPUAccounting=yes -p WorkingDirectory="$H" \
    -p StandardOutput=file:"$O/$m-$d.out" -p StandardError=file:"$O/$m-$d.err" -P bash "$A/tmp-p07/cells-by-day/jall-aug-$d.sh" > "$O/$m-$d.props" 2>&1
  echo "rc=$?"
}
gate() { # день метка
  local d="$1" m="$2" r="$O/ref/body-$d.sha" src="serial"
  body "$d" > "$O/$m-$d.body"
  [ "$d" = 2026-08-01 ] && [ ! -f "$r" ] && cp "$A/tk028/b2/manifest.body.sha" "$r"
  if [ ! -f "$r" ]; then cp "$O/$m-$d.body" "$r"; src="first"; fi
  [ -f "$O/ref/src-$d" ] && src=$(cat "$O/ref/src-$d"); [ ! -f "$O/ref/src-$d" ] && echo "$src" > "$O/ref/src-$d"
  echo "diff=$(diff "$r" "$O/$m-$d.body" | wc -l) ref=$src"
}
if [ -n "$REFD" ]; then
  IFS=, read -ra RL <<< "$REFD"
  t0=$(date +%s.%N)
  for d in "${RL[@]}"; do
    s0=$(date +%s.%N); rc=$(runday "$d" ser); s1=$(date +%s.%N)
    cp /dev/null "$O/ref/src-$d"; echo serial > "$O/ref/src-$d"; body "$d" > "$O/ref/body-$d.sha"
    echo "serial $d $rc wall=$(python3 -c "print(round($s1-$s0,1))") $(grep -hE 'CPU time|Memory peak' "$O/ser-$d.props" | tr '\n' ' ')" >> "$O/summary.txt"
  done
fi
IFS=, read -ra LL <<< "$LANES"
for N in "${LL[@]}"; do
  t0=$(date +%s.%N)
  export -f runday; export TAG A H O
  printf '%s\n' "${DL[@]}" | xargs -P "$N" -I{} bash -c 'runday {} L'"$N"' > "$O/L'"$N"'-{}.rc"'
  t1=$(date +%s.%N)
  g=""; for d in "${DL[@]}"; do g="$g $d:$(gate "$d" "L$N" | tr ' ' '_')"; done
  echo "lanes=$N days=${#DL[@]} wall=$(python3 -c "print(round($t1-$t0,1))")$g" >> "$O/summary.txt"
  for d in "${DL[@]}"; do echo "  L$N $d $(cat "$O/L$N-$d.rc") $(grep -hE 'CPU time|Memory peak' "$O/L$N-$d.props" | tr '\n' ' ')" >> "$O/summary.txt"; done
done
date -Is > "$O/DONE"
