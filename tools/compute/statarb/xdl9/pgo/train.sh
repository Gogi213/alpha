#!/usr/bin/env bash
# TK-493 pgo (В-32): обучающая нагрузка для PGO ядра stat-arb. Запуск на Steam Deck инструментированным бинарём
# (-C profile-generate, build_pgo_vps.sh gen). ТОЛЬКО сутки формирования: панели и фандинг — копии из prep_pform.py,
# обрезанные до суток < 2479 (15.10.2026 00:00 UTC); торговое окно 15.10..04.11 процесс не читает вообще.
#   train.sh <инструментированный бинарь> [потоки=4]
# Состав — типичные боевые команды: run --cells по видам (pair, basket, cross, funding-pp/ps) и биржам, одиночные клетки (--cell),
# --delay-x, правило funding (D3), --rearm / --trades-ext / --ext-out / --job-day-out (G11), --mtm-open / --cells-out / --open-out,
# фандинг в pair/basket/cross; bench (пустой приёмник и --digest, все виды, reference, drift); form (формирование пар).
# Профиль: $PROF/alpha-<хеш модуля>.profraw — все запуски сливаются в один файл (%m).
set -u
BIN=${1:?инструментированный бинарь}; TH=${2:-4}
R=$HOME/xdl9; O=$R/pgo; PF=$O/pform; FF=$O/pfund; J=$R/gates/g6b
PROF=${PROF:-$O/profraw}; OUT=$O/train_out
rm -rf "$PROF" "$OUT"; mkdir -p "$PROF" "$OUT"
export LLVM_PROFILE_FILE="$PROF/alpha-%m.profraw"
CELLS=$J/cells.csv
fail=0; n=0
nb() { local k; k=$(( $(wc -l < "$1") - 1 )); seq -s, 0 $((k-1)); }
go() {  # метка, аргументы statarb...
  local tag=$1; shift
  local t0=$SECONDS
  "$BIN" statarb "$@" > "$OUT/$tag.log" 2>&1 < /dev/null || { echo "ОШИБКА $tag rc=$?"; tail -2 "$OUT/$tag.log"; fail=$((fail+1)); }
  n=$((n+1)); echo "$tag: $((SECONDS-t0)) с"
}
cellargs() {  # строка клетки cells.csv → флаги одиночной клетки
  IFS=, read -r cell window min_obs beta entry exit stop hold refresh <<< "$1"
  CA=(--cell "$cell" --window "$window" --min-obs "$min_obs" --entry-z "$entry" --exit-z "$exit" --stop-z "$stop" --max-hold "$hold" --refresh "$refresh")
  [ -n "$beta" ] && CA+=(--beta-fixed "$beta")
}
echo "обучение: потоков $TH, панели $PF (обрезка по суткам < 2479), бинарь $BIN, профиль $PROF"

# 1. run --cells по видам и биржам (как G6б), без новых флагов
for ex in binance bybit okx bingx bitget kraken aster lighter; do
  go "pair_$ex"   run --kind pair   --jobs "$J/jobs_pair_$ex.csv"   --panels "$PF/$ex" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_pair_$ex.csv")"   --cells "$CELLS" --daily-out "$OUT/p_$ex.daily.csv" --trades-out "$OUT/p_$ex.trades.csv"
  go "basket_$ex" run --kind basket --jobs "$J/jobs_basket_$ex.csv" --panels "$PF/$ex" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_basket_$ex.csv")" --cells "$CELLS" --daily-out "$OUT/b_$ex.daily.csv" --trades-out "$OUT/b_$ex.trades.csv"
done
for b in bybit aster bitget; do
  go "cross_binance_$b" run --kind cross --jobs "$J/jobs_cross_binance_$b.csv" --panels "$PF/binance" --panels-b "$PF/$b" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_cross_binance_$b.csv")" --cells "$CELLS" --daily-out "$OUT/c_$b.daily.csv" --trades-out "$OUT/c_$b.trades.csv"
done

# 1б. крупный набор g10 (120 символов Binance → 7 140 пар; кросс 100 × 62; корзины из тех же символов): основной вес профиля — как в боевом счёте
G10=$R/gates/g10; NB=$PF/bin120
go big_pair        run --kind pair  --jobs "$G10/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --daily-out "$OUT/B.daily.csv"
go big_pair_flags  run --kind pair  --jobs "$G10/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --rearm --trades-ext --trade-jobs 0,1,2,3,4,5 --trades-out "$OUT/B.trades.csv" --daily-out "$OUT/B.daily.csv" --ext-out "$OUT/B.ext.csv" --job-day-out "$OUT/B.jd.csv"
for c in one two three same; do
  go "big_pair_cells_$c" run --kind pair --jobs "$G10/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells_$c.csv" --daily-out "$OUT/B.daily.csv"
done
go big_pair_mtm    run --kind pair  --jobs "$G10/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells_three.csv" --daily-out "$OUT/B.daily.csv" --mtm-open --cells-out "$OUT/B.cells.csv"
go big_cross       run --kind cross --jobs "$G10/jobs_cross100.csv" --panels "$NB" --panels-b "$PF/byb120" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --daily-out "$OUT/B.daily.csv"
go big_cross_rearm run --kind cross --jobs "$G10/jobs_cross100.csv" --panels "$NB" --panels-b "$PF/byb120" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --rearm --daily-out "$OUT/B.daily.csv" --ext-out "$OUT/B.ext.csv"
mapfile -t NM < <(ls "$NB" | sed 's/\.sap$//')
{ echo y,x,from_day,to_day; for i in "${!NM[@]}"; do for d in 1 7 13; do echo "${NM[i]},${NM[(i+d)%${#NM[@]}]}+${NM[(i+d+1)%${#NM[@]}]}+${NM[(i+d+2)%${#NM[@]}]},2465,2479"; done; done; } > "$OUT/jobs_basket_big.csv"
go big_basket      run --kind basket --jobs "$OUT/jobs_basket_big.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --daily-out "$OUT/B.daily.csv"
go big_basket_flags run --kind basket --jobs "$OUT/jobs_basket_big.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --rearm --daily-out "$OUT/B.daily.csv" --ext-out "$OUT/B.ext.csv" --job-day-out "$OUT/B.jd.csv"
go big_pair_fund   run --kind pair  --jobs "$G10/jobs_pair.csv" --panels "$NB" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" --cells "$G10/cells5.csv" --daily-out "$OUT/B.daily.csv"

# 2. одиночные клетки (--cell): путь run_pair; все 5 клеток на двух наборах
for ex in binance okx; do
  for kind in pair basket; do
    while read -r line; do
      cellargs "$line"
      go "single_${kind}_${ex}_${CA[1]}" run --kind "$kind" --jobs "$J/jobs_${kind}_$ex.csv" --panels "$PF/$ex" --min-span 1000 --threads "$TH" "${CA[@]}" --daily-out "$OUT/s.daily.csv" --trades-out "$OUT/s.trades.csv"
    done < <(tail -n +2 "$CELLS")
  done
done

# 3. G11: --rearm, --trades-ext, --ext-out, --job-day-out (с --rearm и без него)
for spec in "pair binance binance -" "pair bitget bitget -" "pair kraken kraken -" "basket okx okx -" "cross binance_bitget binance bitget"; do
  read -r kind tag py pb <<< "$spec"
  B=(); [ "$pb" != "-" ] && B=(--panels-b "$PF/$pb")
  go "g11_rearm_${kind}_$tag" run --kind "$kind" --jobs "$J/jobs_${kind}_$tag.csv" --panels "$PF/$py" "${B[@]}" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_${kind}_$tag.csv")" --trades-ext --rearm --cells "$CELLS" --daily-out "$OUT/g.daily.csv" --trades-out "$OUT/g.trades.csv" --ext-out "$OUT/g.ext.csv" --job-day-out "$OUT/g.jd.csv"
  go "g11_norearm_${kind}_$tag" run --kind "$kind" --jobs "$J/jobs_${kind}_$tag.csv" --panels "$PF/$py" "${B[@]}" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_${kind}_$tag.csv")" --trades-ext --cells "$CELLS" --daily-out "$OUT/g.daily.csv" --trades-out "$OUT/g.trades.csv" --ext-out "$OUT/g.ext.csv" --job-day-out "$OUT/g.jd.csv"
done
# боевая форма: --rearm + выводы без --trade-jobs (сделки всех заданий не пишем — как в счёте), несколько бирж
for ex in bybit okx aster; do
  go "prod_rearm_pair_$ex" run --kind pair --jobs "$J/jobs_pair_$ex.csv" --panels "$PF/$ex" --min-span 1000 --threads "$TH" --rearm --cells "$CELLS" --daily-out "$OUT/q.daily.csv" --ext-out "$OUT/q.ext.csv" --job-day-out "$OUT/q.jd.csv"
done

# 4. --mtm-open / --cells-out / --open-out
for ex in bybit binance; do
  go "mtm_pair_$ex" run --kind pair --jobs "$J/jobs_pair_$ex.csv" --panels "$PF/$ex" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$J/jobs_pair_$ex.csv" | cut -d, -f1-40)" --cells "$CELLS" --daily-out "$OUT/m.daily.csv" --trades-out "$OUT/m.trades.csv" --mtm-open --cells-out "$OUT/m.cells.csv" --open-out "$OUT/m.open.csv"
done

# 5. фандинг: pair/basket (обе ноги из --funding-y), cross (обе биржи), funding-pp / funding-ps, правило funding (D3), задержка ноги X
go "fund_pair_binance"   run --kind pair   --jobs "$J/jobs_pair_binance.csv"   --panels "$PF/binance" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" --cells "$CELLS" --daily-out "$OUT/f.daily.csv"
go "fund_basket_binance" run --kind basket --jobs "$J/jobs_basket_binance.csv" --panels "$PF/binance" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" --cells "$CELLS" --daily-out "$OUT/f.daily.csv"
go "fund_cross"          run --kind cross  --jobs "$J/jobs_cross_binance_bybit.csv" --panels "$PF/binance" --panels-b "$PF/bybit" --funding-y "$FF/binance" --funding-x "$FF/bybit" --min-span 1000 --threads "$TH" --cells "$CELLS" --daily-out "$OUT/f.daily.csv"
go "fund_pp"             run --kind funding-pp --jobs "$J/jobs_cross_binance_bybit.csv" --panels "$PF/binance" --panels-b "$PF/bybit" --funding-y "$FF/binance" --funding-x "$FF/bybit" --min-span 1000 --threads "$TH" --cells "$CELLS" --daily-out "$OUT/f.daily.csv" --trades-out "$OUT/f.trades.csv" --trade-jobs 0,1,2
go "fund_ps"             run --kind funding-ps --jobs "$J/jobs_cross_binance_bybit.csv" --panels "$PF/binance" --panels-b "$PF/bybit" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" --cells "$CELLS" --daily-out "$OUT/f.daily.csv"
while read -r line; do
  cellargs "$line"
  go "d3_pp_${CA[1]}" run --kind funding-pp --jobs "$J/jobs_cross_binance_bybit.csv" --panels "$PF/binance" --panels-b "$PF/bybit" --funding-y "$FF/binance" --funding-x "$FF/bybit" --min-span 1000 --threads "$TH" "${CA[@]}" --rule funding --funding-k 3 --hold-payments 8 --daily-out "$OUT/d.daily.csv" --trades-out "$OUT/d.trades.csv" --trade-jobs 0,1,2
  go "d3_pair_${CA[1]}" run --kind pair --jobs "$J/jobs_pair_binance.csv" --panels "$PF/binance" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" "${CA[@]}" --rule funding --funding-k 3 --hold-payments 8 --daily-out "$OUT/d.daily.csv"
done < <(tail -n +2 "$CELLS" | head -2)
go "delay2_pair"  run --kind pair  --jobs "$J/jobs_pair_binance.csv" --panels "$PF/binance" --min-span 1000 --threads "$TH" --delay-x 2 --cells "$CELLS" --daily-out "$OUT/y.daily.csv" --trades-out "$OUT/y.trades.csv" --trade-jobs 0,1,2
go "delay1_cross" run --kind cross --jobs "$J/jobs_cross_binance_bybit.csv" --panels "$PF/binance" --panels-b "$PF/bybit" --min-span 1000 --threads "$TH" --delay-x 1 --cells "$CELLS" --daily-out "$OUT/y.daily.csv"

# 6. bench: пустой приёмник (скорость) и --digest, все виды, reference и drift (панели формирования, а не полные)
C1="--window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0"
C2="--window 1440 --min-obs 720 --entry-z 1.5 --exit-z 0.2 --max-hold 480 --refresh 97"
COM="--engine kernel --max-jobs 100000 --min-span 2000 --threads $TH"
FY="--funding-y $FF/bybit"; FX="--funding-x $FF/binance"
# shellcheck disable=SC2086
{
go bench_pair_c1_null   bench --kind pair   $COM --panels "$PF/bin120" --symbols 120 $C1
go bench_pair_c2_null   bench --kind pair   $COM --panels "$PF/bin120" --symbols 120 $C2
go bench_pair_c1        bench --kind pair   $COM --panels "$PF/byb50" --symbols 50 $C1 --digest
go bench_pair_c2        bench --kind pair   $COM --panels "$PF/byb50" --symbols 50 $C2 --digest
go bench_pair_rearm     bench --kind pair   $COM --panels "$PF/byb50" --symbols 50 $C1 --rearm --digest
go bench_cross_c1       bench --kind cross  $COM --panels "$PF/byb50" --panels-b "$PF/binance" --symbols 50 $C1 --digest
go bench_cross_c2_null  bench --kind cross  $COM --panels "$PF/byb50" --panels-b "$PF/binance" --symbols 50 $C2
go bench_basket3_c1     bench --kind basket $COM --panels "$PF/byb50" --symbols 50 --basket-k 3 $C1 --digest
go bench_basket3_c2_null bench --kind basket $COM --panels "$PF/bin120" --symbols 120 --basket-k 3 $C2
go bench_fundpp_c1      bench --kind funding-pp $COM --panels "$PF/byb50" --panels-b "$PF/binance" $FY $FX --symbols 50 $C1 --digest
go bench_fundpp_c2_null bench --kind funding-pp $COM --panels "$PF/byb50" --panels-b "$PF/binance" $FY $FX --symbols 50 $C2
go bench_fundpp_d3      bench --kind funding-pp $COM --panels "$PF/byb50" --panels-b "$PF/binance" $FY $FX --symbols 50 $C1 --funding-k 3 --hold-payments 8 --digest
go bench_fundps_c1      bench --kind funding-ps $COM --panels "$PF/byb50" --panels-b "$PF/binance" $FY --symbols 50 $C1 --digest
go bench_pair_delay2    bench --kind pair   $COM --panels "$PF/byb50" --symbols 50 $C1 --delay-x 2 --digest
go bench_cross_delay1   bench --kind cross  $COM --panels "$PF/byb50" --panels-b "$PF/binance" --symbols 50 $C1 --delay-x 1 --digest
go bench_reference      bench --kind pair   --engine reference --panels "$PF/byb50" --symbols 12 --max-jobs 100000 --min-span 2000 --threads "$TH" $C1 --digest
go bench_drift          bench --kind pair   --engine drift --panels "$PF/byb50" --symbols 12 --max-jobs 100000 --min-span 2000 --threads "$TH" $C1
}

# 7. form: формирование пар по окну 7 суток внутри формирования (торговля — последние 7 суток формирования)
go form_bin120 form --panels "$PF/bin120" --from-day 2472 --to-day 2479 --window-days 7 --block-chunks 8 --min-obs 1000 --alpha 0.05 --threads "$TH" --jobs-out "$OUT/form.jobs.csv" --detail-out "$OUT/form.detail.csv"
go form_byb120 form --panels "$PF/byb120" --from-day 2472 --to-day 2479 --window-days 7 --block-chunks 4 --min-obs 1000 --alpha 0.10 --threads "$TH" --jobs-out "$OUT/form2.jobs.csv"

echo "запусков $n, ошибок $fail"
ls -l "$PROF"
md5sum "$PROF"/*.profraw 2>/dev/null
exit $fail
