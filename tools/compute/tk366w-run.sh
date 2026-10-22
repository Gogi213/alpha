#!/usr/bin/env bash
# TK-366 (G-016), неделя 15–21.10.2026 (слово владельца 23.10 ~03:05): стакан → сделки (≤4 потока, бережно) → импорт NX → verify. Одна заявка alsched.
#   tk366w-run.sh        env: NX(умолч. 16) PO(потоки стакана, 32) PT(потоки сделок, 4) BIN
export TKD=/srv/alpha-sda/tk366w; D=$TKD; NX=${NX:-16}; PO=${PO:-32}; PT=${PT:-4}
export BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}
cd "$D" || exit 1; mkdir -p log raw tmp/i
t0=$(date +%s); ph() { echo "$(date +%T) [$(( $(date +%s)-t0 )) с] $*"; }
ph "список"; python3 "$D/tk366w-gen.py" "$D/fut-coins.csv" "$D/spot-coins.csv" "$D/instruments.csv" "$D"
touch skip-tr; rm -f ban
ph "фаза 1: стакан, P=$PO"; bash "$D/tk366-dl.sh" "$PO"
rm -f skip-tr
for i in 1 2 3 4 5 6; do   # фаза 2: сделки, P=PT; 403 → флаг ban, пауза 10 мин, новый круг
  rm -f ban; ph "фаза 2: сделки, круг $i, P=$PT"; bash "$D/tk366-dl.sh" "$PT"
  n=$(awk -F'\t' '$4=="dl"||$4 ~ /^missing/ {d[$1 FS $2 FS $3]=1} $4=="ob-only"||$4 ~ /^fail:tr/ {k[$1 FS $2 FS $3]=1} END{c=0; for(x in k) if(!(x in d)) c++; print c}' log/dl.tsv)
  ph "круг $i: без сделок $n"; [ "$n" = 0 ] && break; [ -f ban ] && sleep 600
done
ph "фаза 3: импорт NX=$NX"; bash "$D/tk366-imp.sh" "$NX"
ph "фаза 4: verify"
for mk in fut spot; do
  r=$D/bin-$mk/e-2026-10; [ -d "$r" ] || continue
  ls "$r" | grep -a '\.binlog$' | sed 's/-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]\(-p[0-9]*\)\?\.binlog$//' | sort -u |
    xargs -r -P "$NX" -I{} bash -c '"$BIN" lob verify --symbol {} --root "$0" > "$0/verify-{}.log" 2>&1; printf "%s\t%s\t%s\t%s\n" "$1" {} $? "$(cat "$0/verify-{}.status" 2>/dev/null || echo none)" >> "$2"' "$r" "$mk" "$D/log/verify.tsv"
done
ph "готово: dl $(awk -F'\t' '$4=="dl"' log/dl.tsv | wc -l), import ok $(awk -F'\t' '$4=="ok"' log/import2.tsv | wc -l), verify ok $(awk -F'\t' '$4=="ok"' log/verify.tsv | wc -l) из $(wc -l < log/verify.tsv)"
