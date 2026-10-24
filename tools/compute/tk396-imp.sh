#!/usr/bin/env bash
# TK-396 (G-016), шаг 2: импорт 22.10 (скачано tk396-dl.sh) бинарём master + verify монет, у которых появились сутки 22.10; свод охвата в log/tk396-cover.tsv.
#   tk396-imp.sh        env: NX(15) BIN(/data/tk366b/alpha-tk366b-plain)
export TKD=/srv/alpha-sda/tk366w; D=$TKD; NX=${NX:-15}; export BIN=${BIN:-/data/tk366b/alpha-tk366b-plain}
cd "$D" || exit 1; mkdir -p log raw tmp/i
t0=$(date +%s); ph() { echo "$(date +%T) [$(( $(date +%s)-t0 )) с] $*"; }
ph "импорт NX=$NX BIN=$BIN"; bash "$D/tk366-imp.sh" "$NX"
ph "импорт 22.10: ok $(awk -F'\t' '$3=="2026-10-22" && $4=="ok"' log/import2.tsv | sort -u | wc -l), fail $(awk -F'\t' '$3=="2026-10-22" && $4 ~ /^fail/' log/import2.tsv | sort -u | wc -l)"
: > log/verify-d22.tsv
for mk in fut spot; do
  r=$D/bin-$mk/e-2026-10; [ -d "$r" ] || continue
  ls "$r" | grep -a -- '-2026-10-22\(-p[0-9]*\)\?\.binlog$' | sed 's/-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]\(-p[0-9]*\)\?\.binlog$//' | sort -u |
    xargs -r -P "$NX" -I{} bash -c '"$BIN" lob verify --symbol {} --root "$0" > "$0/verify-{}.log" 2>&1; printf "%s\t%s\t%s\t%s\n" "$1" {} $? "$(cat "$0/verify-{}.status" 2>/dev/null || echo none)" >> "$2"' "$r" "$mk" "$D/log/verify-d22.tsv"
done
ph "verify: ok $(awk -F'\t' '$4=="ok"' log/verify-d22.tsv | wc -l) из $(wc -l < log/verify-d22.tsv)"
# охват: рынок, символ, суток в бинлогах (15–22.10)
for mk in fut spot; do ls "$D/bin-$mk/e-2026-10" | grep -a '\.binlog$' | sed 's/-\(2026-10-[0-9][0-9]\)\(-p[0-9]*\)\?\.binlog$/\t\1/' | sort -u | awk -F'\t' -v m=$mk '{n[$1]++; if($2=="2026-10-22") d22[$1]=1} END{for(s in n) print m "\t" s "\t" n[s] "\t" (s in d22 ? 1 : 0)}'; done | sort > log/tk396-cover.tsv
ph "охват: $(wc -l < log/tk396-cover.tsv) монет; с 22.10: $(awk -F'\t' '$4==1' log/tk396-cover.tsv | wc -l)"
