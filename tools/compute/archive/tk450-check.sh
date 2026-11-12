#!/usr/bin/env bash
# TK-450: проверка сдачи свечей Binance USDⓈ-M 1m. usage: bash tools/compute/tk450-check.sh  (из репо; по ssh на calc)
# 1) число .csv.gz == строк map.csv с символом; 2) verify.csv: все ok=1; 3) сумма минут/пропусков coverage.csv есть в отчёте;
# 4) строки BTC/ICP/PUMP/1000PEPE в .csv.gz == минутам coverage.csv, t_open_ms строго возрастает.
set -eu
REPO=$(cd "$(dirname "$0")/../.." && pwd)
REP="$REPO/docs/findings/binance-klines-1m-2026-10-30.md"
fail=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }

out=$(ssh calc bash -s <<'EOF'
cd /srv/alpha-sda/candles-1m/binance-um
echo "files=$(ls *.csv.gz | wc -l)"
awk -F, 'NR>1 && $2!=""{n++} END{print "mapsym=" n}' map.csv
awk -F, 'NR>1{t++; if($4+0!=1)b++} END{print "verify_rows=" t; print "verify_bad=" b+0}' verify.csv
awk -F, 'NR>1{m+=$4;g+=$5} END{print "minutes=" m; print "gaps=" g}' coverage.csv
for s in BTCUSDT ICPUSDT PUMPUSDT 1000PEPEUSDT; do
  awk -F, -v s=$s '$2==s{print "cov_" s "=" $4}' coverage.csv
  zcat $s.csv.gz | awk -F, -v s=$s 'NR>1{n++; if(n>1 && $1+0<=p)b++; p=$1+0} END{print "rows_" s "=" n; print "order_bad_" s "=" b+0}'
done
EOF
)
echo "$out"
val() { echo "$out" | sed -n "s/^$1=//p" | head -1; }
sp() { printf '%s' "$1" | sed ':a;s/\([0-9]\)\([0-9]\{3\}\)\($\| \)/\1 \2\3/;ta'; }

[ "$(val files)" = "$(val mapsym)" ] && ok "csv.gz $(val files) == монет с символом в map.csv" || bad "csv.gz $(val files) != map $(val mapsym)"
[ "$(val verify_bad)" = "0" ] && ok "verify.csv: $(val verify_rows) zip, расхождений 0" || bad "verify.csv расхождений $(val verify_bad)"
m=$(val minutes); g=$(val gaps)
grep -q "$(sp "$m")" "$REP" && ok "минут $m ($(sp "$m")) есть в отчёте" || bad "минут $m ($(sp "$m")) нет в отчёте"
grep -q "$(sp "$g")" "$REP" && ok "пропусков $g ($(sp "$g")) есть в отчёте" || bad "пропусков $g ($(sp "$g")) нет в отчёте"
for s in BTCUSDT ICPUSDT PUMPUSDT 1000PEPEUSDT; do
  if [ "$(val rows_$s)" = "$(val cov_$s)" ] && [ "$(val order_bad_$s)" = "0" ]; then
    ok "$s: строк $(val rows_$s) == coverage, порядок ок"
  else
    bad "$s: строк $(val rows_$s) vs coverage $(val cov_$s), нарушений порядка $(val order_bad_$s)"
  fi
done
[ "$fail" = 0 ] && echo "TK-450 CHECK: PASS" || { echo "TK-450 CHECK: FAIL"; exit 1; }
