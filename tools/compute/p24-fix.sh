#!/usr/bin/env bash
# TK-344 П-24: падение 16 суток при --exit-group on (повторный id ордера, review tk344-p24-run-fail). А/Б на одних успешных сутках
# (on из run/ против off), равенство → 16 упавших суток досчитать с EG=off; иначе — стоп (баг Инженеру).
P=/data/tk0344/p24; NX=${NX:-15}; cd "$P"
ab=2026-02-20; export EG=off
O=$P/ab bash $P/p24-day.sh "$ab" || { echo "А/Б: off на $ab УПАЛ" >&2; exit 5; }
if diff -rq -x manifest.txt "$P/run/$ab/out" "$P/ab/$ab/out" > ab-diff.txt; then echo "AB_EQUAL $ab" > ab.result
else echo "AB_DIFF $ab" > ab.result; echo "А/Б не равно" >&2; exit 6; fi
mv -n run/fail.txt fail-exitgroup-on.txt
awk '{print $1}' fail-exitgroup-on.txt > fixdays.txt
export O=$P/run
xargs -P "$NX" -I{} bash $P/p24-day.sh {} < fixdays.txt
n=$(ls $O/*/done 2>/dev/null | wc -l); echo "done=$n/$(wc -l < days.txt)"
[ "$n" -eq "$(wc -l < days.txt)" ] && touch $P/run.done
