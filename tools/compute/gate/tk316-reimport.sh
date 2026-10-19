#!/usr/bin/env bash
# TK-316: переимпорт PEPE/BONK/SHIB/MOG новым бинарём (размер в лотах + единица) → verify → гейт BONK → verify 4 монет × месяцы.
# Запуск на calc ТОЛЬКО через alsched (kind build, 1 ядро — 16-е при идущем счёте):
#   python3 /data/sched/alsched.py submit --kind build --cores 1 --mem 4 --cwd /root --adhoc tk316-reimp --reason "переимпорт 4 монет TK-316" --max-runtime 6h --name tk316-reimp -- bash /data/tk316/tk316-reimport.sh
# Выход: $R=/srv/alpha-sda/binlog-spot-tk316/e-YYYY-MM, таблица $W/reimp/verify-table.tsv (месяц, монета, суток, verify rc, status, причина), $W/reimp/summary.txt.
# Зелёный гейт BONK = импорт 2026-01-01 rc=0, verify status=ok, bounce-grid rc=0, «символов 0» нет, кругов (rounds.csv > 2 строк) > 0.
set -uo pipefail
W=/data/tk316; R=/srv/alpha-sda/binlog-spot-tk316; NEW=$W/alpha-new; SRC=/srv/alpha-sda/tk305/spot; D=$W/reimp; SI=$W/spot-import.sh
export OUT=$R BIN=$NEW JOBS=1 LOG=$R/import.tsv TMP=/dev/shm/spot-import-tk316
mkdir -p "$R" "$D"; rm -f "$D/summary.txt" "$D/reimp.done" "$D/verify-table.tsv" "$R/import.tsv" "$R/import-verify.tsv"
say() { echo "$*" | tee -a "$D/summary.txt"; }
grid() {  # $1 бинарь $2 корень $3 символ $4 сутки $5 лот e9 $6 порог usd $7 выход
  "$1" lob bounce-grid --root "$2" --symbol "$3" --day "$4" --queue-model prob:3 \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --stop-form at --stop-form pct1.5 --take-form 1to1 --take-form tr1x1 --deadline-secs 3600 \
    --h3-mode notional --h3-usd "$6" --order-qty-e9 "$5" --threads 1 --driver setups --out-dir "$7" > "$7.log" 2>&1
}
root_of() {  # $1 каталог частей $2 символ $3 сутки $4 корень
  rm -rf "$4"; mkdir -p "$4"; ln -s "$1/$2-$3.binlog" "$4/"; ln -s "$1/verify-$2.status" "$4/"
  echo '{"start_hour_utc":0,"closed":true,"binlog_files":[]}' > "$4/session.json"; [ -f "$1/instruments.csv" ] && ln -s "$1/instruments.csv" "$4/"
}
# 1) гейт BONK: 1 сутки новым бинарём
echo "BONKUSDT 2026-01-01" > "$D/list.1"
bash "$SI" import "$D/list.1"; echo "2026-01 BONKUSDT" > "$D/pairs.1"; bash "$SI" verify-pairs "$D/pairs.1"
st=$(cat "$R/e-2026-01/verify-BONKUSDT.status" 2>/dev/null || echo none)
say "bonk-import: $(grep -a '^BONKUSDT.2026-01-01' "$R/import.tsv" | cut -f3,4,5 | tr '\t' ' ')  verify=$st"
root_of "$R/e-2026-01" BONKUSDT 2026-01-01 "$D/rt-bonk"; rm -rf "$D/new-bonk"
grid "$NEW" "$D/rt-bonk" BONKUSDT 2026-01-01 1000000000000000 1000 "$D/new-bonk"; rc=$?
rounds=$(wc -l < "$D/new-bonk/rounds.csv" 2>/dev/null || echo 0); zero=$(grep -ac 'символов 0' "$D/new-bonk.log")
say "bonk-grid rc=$rc rounds_lines=$rounds символов0=$zero"
ok=1; { [ "$st" = ok ] && [ "$rc" = 0 ] && [ "$rounds" -gt 2 ] && [ "$zero" = 0 ]; } || ok=0
say "GATE-BONK $([ $ok = 1 ] && echo ЗЕЛЁНЫЙ || echo КРАСНЫЙ)"
# 2) все сутки 4 монет по месяцам: импорт → verify → таблица
for s in BONKUSDT PEPEUSDT SHIBUSDT MOGUSDT; do ls "$SRC/ob/$s" | sed -E "s/^([0-9-]+)_.*/$s \1/"; done > "$D/list.all"
for m in $(cut -d' ' -f2 "$D/list.all" | cut -c1-7 | sort -u); do
  grep -E " $m-" "$D/list.all" > "$D/list.$m"
  bash "$SI" import "$D/list.$m"; bash "$SI" verify "$m"
  say "month $m: импорт $(grep -c " $m-" "$D/list.$m") суток, не-ok импорта: $(awk -F'\t' -v m="$m" '$2 ~ "^"m && $3!="0" && $3!="skip"' "$R/import.tsv" | wc -l)"
done
printf 'месяц\tмонета\tсуток\tverify_rc\tstatus\tпричина\n' > "$D/verify-table.tsv"
while IFS=$'\t' read -r m s vrc stv; do
  n=$(ls "$R/e-$m" 2>/dev/null | grep -ac "^$s-.*binlog$")
  why=""; [ "$stv" != ok ] && why=$(tail -3 "$R/e-$m/verify-$s.log" 2>/dev/null | tr '\n\t' '  ' | cut -c1-300)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$m" "$s" "$n" "$vrc" "$stv" "$why"
done < <(sort "$R/import-verify.tsv") >> "$D/verify-table.tsv"
say "verify: ok=$(awk -F'\t' 'NR>1 && $5=="ok"' "$D/verify-table.tsv" | wc -l) fail=$(awk -F'\t' 'NR>1 && $5!="ok"' "$D/verify-table.tsv" | wc -l) пар=$(($(wc -l < "$D/verify-table.tsv")-1))"
say "импорт rc≠0: $(awk -F'\t' '$3!="0" && $3!="skip"' "$R/import.tsv" | wc -l) из $(wc -l < "$R/import.tsv")"
touch "$D/reimp.done"
[ $ok = 1 ]
