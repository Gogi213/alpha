#!/usr/bin/env bash
# TK-344 П-24 §6: прогон 8 клеток × сутки янв–сен; под alsched (NX ядер). Сначала самые тяжёлые сутки одной (проба: rc и число форм), затем xargs -P NX.
P=/data/tk0344/p24; NX=${NX:-15}; export O=$P/run; mkdir -p "$O"; cd "$P"
cat > cells.txt <<'C'
before-1to1-1800-eat50-early2 all
pct0.1-1to1-1800-eat50-early2 all
pct0.5-1to1-1800-eat50-early2 all
before-1to1-1800-gone50-early2 all
before-1to1-1800-early2 all
before-1to1-1800-eat50 all
before-1to1-1800-eat50-early1 all
before-1to1-14400-eat50-early2 all
C
awk -F, 'NR>1&&$3~/^2026-0[1-9]$/&&$17=="пускаем"&&$1!="TRXUSDT"{n[$2]++} END{for(d in n)print n[d],d}' /data/tk044/final3/verdict.csv | sort -rn | awk '{print $2}' > days.txt
echo "days: $(wc -l < days.txt)"
first=$(head -1 days.txt)
bash $P/p24-day.sh "$first" || { echo "ПРОБА $first УПАЛА" >&2; exit 5; }
[ -s "$O/$first/out/forms.csv" ] && echo "forms=$(($(wc -l < $O/$first/out/forms.csv)-1))" || echo "forms.csv нет" >&2
tail -n +2 days.txt | xargs -P "$NX" -I{} bash $P/p24-day.sh {}
n=$(ls $O/*/done 2>/dev/null | wc -l); echo "done=$n/$(wc -l < days.txt)"
[ "$n" -eq "$(wc -l < days.txt)" ] && touch $P/run.done
