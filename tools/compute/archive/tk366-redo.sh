#!/usr/bin/env bash
# TK-366 (G-016), возврат Судьи 23.10: перегнать упавшие в импорте монето-сутки бинарём master (TK-316, qty i128) и verify их монет.
#   tk366-redo.sh    env: TKD (/srv/alpha-sda/tk366w) NX (4) BIN (/data/tk366b/alpha-tk366b-plain). Сырьё упавших осталось в raw/ (imp-one удаляет его только после ok).
D=${TKD:-/srv/alpha-sda/tk366w}; NX=${NX:-4}; export BIN=${BIN:-/data/tk366b/alpha-tk366b-plain}; export TKD=$D
cd "$D" || exit 1
awk -F'\t' '$4 ~ /^fail/ {f[$1 FS $2 FS $3]=1} $4=="ok" {delete f[$1 FS $2 FS $3]} END{for(k in f) print k}' log/import2.tsv |
  awk -F'\t' -v r="$D/raw" '{print $1, $2, $3, r}' | sort > log/redo-list.txt
wc -l < log/redo-list.txt | sed 's/^/к перегону: /'
xargs -a log/redo-list.txt -r -P "$NX" -n 4 bash "$D/tk366-imp-one.sh"
for mk in fut spot; do
  r=$D/bin-$mk/e-2026-10; [ -d "$r" ] || continue
  awk -v m=$mk '$1==m{print $2}' log/redo-list.txt | sort -u | while read -r s; do
    "$BIN" lob verify --symbol "$s" --root "$r" > "$r/verify-$s.log" 2>&1
    printf '%s\t%s\t%s\t%s\n' "$mk" "$s" $? "$(cat "$r/verify-$s.status" 2>/dev/null || echo none)" >> log/verify-redo.tsv
  done
done
echo "redo готово: import ok $(awk -F'\t' '$4=="ok"' log/import2.tsv | wc -l), verify-redo $(wc -l < log/verify-redo.tsv)"
