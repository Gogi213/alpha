#!/usr/bin/env bash
# TK-396 (G-016), шаг 1: докачка 22.10 по списку монет TK-366 (стакан + сделки, ≤4 потока, при 403 — пауза и новый круг) + сверка размеров файлов биржи
# с журналом недели для 104 монет, исключённых при приёмке TK-366 (HEAD-запросы: файл на бирже не менялся → закачка не причина). env: PT(4), TK_DAYS(2026-10-22)
export TKD=/srv/alpha-sda/tk366w; D=$TKD; PT=${PT:-4}; export TK_DAYS=${TK_DAYS:-2026-10-22}
cd "$D" || exit 1; mkdir -p log raw tmp/i
t0=$(date +%s); ph() { echo "$(date +%T) [$(( $(date +%s)-t0 )) с] $*"; }
for mk in fut spot; do [ -f $mk-todo.week.txt ] || cp $mk-todo.txt $mk-todo.week.txt; done
ph "список $TK_DAYS"; python3 "$D/tk366w-gen.py" "$D/fut-coins.csv" "$D/spot-coins.csv" "$D/instruments.csv" "$D"
rm -f ban skip-tr
for i in 1 2 3 4 5 6; do
  rm -f ban; ph "закачка, круг $i, P=$PT"; bash "$D/tk366-dl.sh" "$PT"
  n=$(awk -F'\t' -v d="$TK_DAYS" '$3==d && ($4=="dl"||$4 ~ /^missing/) {d1[$1 FS $2]=1} $3==d && $4 ~ /^fail/ {k[$1 FS $2]=1} END{c=0; for(x in k) if(!(x in d1)) c++; print c}' log/dl.tsv)
  ph "круг $i: без файлов $n"; [ "$n" = 0 ] && break; [ -f ban ] && sleep 600
done
ph "сверка размеров 104 исключённых (HEAD)"
awk -F'\t' '$4=="dl"{o[$1 FS $2 FS $3]=$5 FS $6} END{for(k in o) print k FS o[k]}' OFS='\t' log/dl.tsv | sort > log/tk396-exp.tsv
awk -F'\t' 'NR==FNR{f[$1 FS $2]=1;next} ($1 FS $2) in f && $3<"2026-10-22"' /tmp/f104.tsv log/tk396-exp.tsv > log/tk396-head-todo.tsv
wc -l < log/tk396-head-todo.tsv | sed 's/^/HEAD: /'
: > log/tk396-head.tsv
xargs -a log/tk396-head-todo.tsv -r -P "$PT" -d '\n' -I{} bash -c '
  IFS=$'"'"'\t'"'"' read -r mk sym d eo et <<< "$1"
  if [ "$mk" = fut ]; then uo="https://quote-saver.bycsi.com/orderbook/linear/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/trading/$sym/$sym$d.csv.gz"
  else uo="https://quote-saver.bycsi.com/orderbook/spot/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/spot/$sym/${sym}_$d.csv.gz"; fi
  no=$(curl -sIL --connect-timeout 20 --retry 3 "$uo" | tr -d "\r" | awk -F": " "tolower(\$1)==\"content-length\"{l=\$2} END{print l+0}")
  nt=$(curl -sIL --connect-timeout 20 --retry 3 "$ut" | tr -d "\r" | awk -F": " "tolower(\$1)==\"content-length\"{l=\$2} END{print l+0}")
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$mk" "$sym" "$d" "$eo" "$no" "$et" "$nt" "$([ "$eo" = "$no" ] && [ "$et" = "$nt" ] && echo same || echo DIFF)" >> log/tk396-head.tsv' _ {}
ph "HEAD: $(grep -c same log/tk396-head.tsv) same, $(grep -c DIFF log/tk396-head.tsv) DIFF из $(wc -l < log/tk396-head.tsv)"
ph "готово: 22.10 dl $(awk -F'\t' -v d="$TK_DAYS" '$3==d && $4=="dl"' log/dl.tsv | sort -u | wc -l), missing $(awk -F'\t' -v d="$TK_DAYS" '$3==d && $4 ~ /^missing/' log/dl.tsv | wc -l)"
