#!/usr/bin/env bash
# TK-366 (G-016): перегон спот-суток недели 15–21.10, упавших в импорте бинарём до TK-316, бинарём master; затем verify этих монет.
#   tk366w-reimp.sh        env: NX(6) BIN(/data/tk0366/alpha-tk316-plain) — сырьё остаётся в raw от упавших, журнал import2.tsv дописывается
export TKD=/srv/alpha-sda/tk366w; D=$TKD; NX=${NX:-6}; export BIN=${BIN:-/data/tk0366/alpha-tk316-plain}
cd "$D" || exit 1
awk -F'\t' '$1=="spot" && $4=="ok" {ok[$2 FS $3]=1} $1=="spot" && $4 ~ /^fail/ {f[$2 FS $3]=1} END{for(k in f) if(!(k in ok)) print k}' log/import2.tsv | sort > log/reimp-todo.tsv
echo "к перегону: $(wc -l < log/reimp-todo.tsv)"
sed "s|\$| $D/raw|; s|^|spot |; s|\t| |" log/reimp-todo.tsv | xargs -r -P "$NX" -n 4 bash "$D/tk366-imp-one.sh"
echo "импорт: ok $(awk -F'\t' '$4=="ok"' log/import2.tsv | wc -l) (журнал всего), остаток fail:"
awk -F'\t' '$1=="spot" && $4=="ok" {ok[$2 FS $3]=1} END{while((getline l < "log/reimp-todo.tsv")>0) if(!(l in ok)) print l}' log/import2.tsv
r=$D/bin-spot/e-2026-10; : > log/verify-reimp.tsv
cut -f1 log/reimp-todo.tsv | sort -u | xargs -r -P "$NX" -I{} bash -c '"$BIN" lob verify --symbol {} --root "$0" > "$0/verify-{}.log" 2>&1; printf "spot\t%s\t%s\t%s\n" {} $? "$(cat "$0/verify-{}.status" 2>/dev/null || echo none)" >> "$1"' "$r" "$D/log/verify-reimp.tsv"
cat log/verify-reimp.tsv
