#!/usr/bin/env bash
# TK-396 (G-016), шаг 2б: спот-сутки 22.10, упавшие в импорте (BadNumber level size / строка N не разбирается), — перегон бинарём BIN (с TK-316) + verify этих монет.
#   tk396-reimp.sh        env: NX(4) BIN(/data/tk0366/alpha-tk316-plain)
export TKD=/srv/alpha-sda/tk366w; D=$TKD; NX=${NX:-4}; export BIN=${BIN:-/data/tk0366/alpha-tk316-plain}
cd "$D" || exit 1
awk -F'\t' '$1=="spot" && $4=="ok" {ok[$2 FS $3]=1} $1=="spot" && $4 ~ /^fail/ {f[$2 FS $3]=1} END{for(k in f) if(!(k in ok)) print k}' log/import2.tsv | sort > log/reimp-todo-d22.tsv
echo "к перегону: $(wc -l < log/reimp-todo-d22.tsv)"
sed "s|\$| $D/raw|; s|^|spot |; s|\t| |" log/reimp-todo-d22.tsv | xargs -r -P "$NX" -n 4 bash "$D/tk366-imp-one.sh"
echo "остаток fail:"
awk -F'\t' '$1=="spot" && $4=="ok" {ok[$2 FS $3]=1} END{while((getline l < "log/reimp-todo-d22.tsv")>0) if(!(l in ok)) print l}' log/import2.tsv
r=$D/bin-spot/e-2026-10; : > log/verify-reimp-d22.tsv
cut -f1 log/reimp-todo-d22.tsv | sort -u | xargs -r -P "$NX" -I{} bash -c '"$BIN" lob verify --symbol {} --root "$0" > "$0/verify-{}.log" 2>&1; printf "spot\t%s\t%s\t%s\n" {} $? "$(cat "$0/verify-{}.status" 2>/dev/null || echo none)" >> "$1"' "$r" "$D/log/verify-reimp-d22.tsv"
cat log/verify-reimp-d22.tsv
