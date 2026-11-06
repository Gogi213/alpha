#!/usr/bin/env bash
# TK-493 F6, G6 (часть а): новый бинарь против tk479@f27c879e — `statarb bench --digest` на 50 длиннейших панелях Bybit.
# Запуск на Steam Deck. Хеш и счётчики обязаны совпасть побайтно; время и скорость из сравнения исключаются.
#   g6a_bench_digest.sh [потоки]   (по умолчанию 4: на Deck в это время считают другие)
set -u
BIN_A=$HOME/xdl9/bin/statarb-tk479-f27c879e
BIN_N=${BIN_N:-$HOME/xdl9/bin/statarb-xdl9-02c6a0df}   # TK-493 w3: BIN_N / G6A_DIR переопределяют бинарь и каталог (новая сборка против эталонных sha256)
P=$HOME/xdl9/panels
F=$HOME/xdl9/funding
G=${G6A_DIR:-$HOME/xdl9/gates/g6}
TH=${1:-4}
OUT=$G/g6a
mkdir -p "$OUT"

# 1. Набор: 50 длиннейших панелей Bybit = по убыванию (минут панели, валидных минут), затем по имени.
python3 - "$P/manifest.csv" "$P/bybit" "$P/binance" "$G" <<'PY'
import csv, os, sys
man, pb, pbin, g = sys.argv[1:5]
rows = [r for r in csv.DictReader(open(man, encoding="utf-8"))
        if r["exchange"] == "bybit" and r["panel"] == "1" and r["panel_minutes"]]
rows.sort(key=lambda r: (-int(r["panel_minutes"]), -int(r["panel_valid"]), r["symbol"]))
top = rows[:50]
for d in ("byb50", "bin50"):
    os.makedirs(os.path.join(g, d), exist_ok=True)
    for f in os.listdir(os.path.join(g, d)):
        os.unlink(os.path.join(g, d, f))
nb = 0
with open(os.path.join(g, "byb50.csv"), "w") as w:
    w.write("symbol,panel_minutes,panel_valid,in_binance\n")
    for r in top:
        s = r["symbol"]
        os.symlink(os.path.join(pb, s + ".sap"), os.path.join(g, "byb50", s + ".sap"))
        inb = os.path.exists(os.path.join(pbin, s + ".sap"))
        if inb:
            os.symlink(os.path.join(pbin, s + ".sap"), os.path.join(g, "bin50", s + ".sap"))
            nb += 1
        w.write(f"{s},{r['panel_minutes']},{r['panel_valid']},{int(inb)}\n")
print(f"byb50: {len(top)} панелей (минут панели {top[-1]['panel_minutes']}..{top[0]['panel_minutes']}), из них есть на Binance: {nb}")
PY

# 2. Прогоны: одинаковые аргументы у обоих бинарей. Строка сравнения = всё, кроме времени ("счёт … пар/с").
cmp_line() { sed -E 's/, счёт [0-9.]+ с \(загрузка [0-9.]+ с, /, (/; s/\), [0-9.e+-]+ строк\/с, [0-9.]+ пар\/с$/)/' ; }
fail=0; n=0
run_case() {  # имя, аргументы...
  local name=$1; shift
  for tag in A N; do
    local bin=$BIN_A; [ $tag = N ] && bin=$BIN_N
    "$bin" statarb bench "$@" > "$OUT/$name.$tag.raw" 2>&1; echo "rc=$?" >> "$OUT/$name.$tag.raw"
    cmp_line < "$OUT/$name.$tag.raw" > "$OUT/$name.$tag.cmp"
  done
  n=$((n+1))
  if cmp -s "$OUT/$name.A.cmp" "$OUT/$name.N.cmp" && grep -q '^digest ' "$OUT/$name.N.cmp" && grep -q '^rc=0' "$OUT/$name.N.cmp"; then
    echo "РАВНО  $name: $(grep '^digest ' "$OUT/$name.N.cmp") | $(grep -o 'заданий [0-9]* из [0-9]*, минут [0-9]*, пропусков [0-9]*, сделок [0-9]*' "$OUT/$name.N.cmp")"
  else
    echo "РАЗНО  $name"; diff "$OUT/$name.A.cmp" "$OUT/$name.N.cmp" | head -8; fail=$((fail+1))
  fi
}
CELL1="--window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --max-hold 720 --refresh 0"
CELL2="--window 1440 --min-obs 720 --entry-z 1.5 --exit-z 0.2 --max-hold 480 --refresh 97"
COMMON="--engine kernel --panels $G/byb50 --symbols 50 --max-jobs 100000 --min-span 2000 --threads $TH"
B="--panels-b $G/bin50"
FY="--funding-y $F/bybit"
FX="--funding-x $F/binance"
run_case pair_c1        --kind pair   $COMMON $CELL1 --digest
run_case pair_c2        --kind pair   $COMMON $CELL2 --digest
run_case cross_c1       --kind cross  $COMMON $B $CELL1 --digest
run_case cross_c2       --kind cross  $COMMON $B $CELL2 --digest
run_case basket3_c1     --kind basket $COMMON --basket-k 3 $CELL1 --digest
run_case basket3_c2     --kind basket $COMMON --basket-k 3 $CELL2 --digest
run_case fundpp_c1      --kind funding-pp $COMMON $B $FY $FX $CELL1 --digest
run_case fundpp_c2      --kind funding-pp $COMMON $B $FY $FX $CELL2 --digest
run_case fundpp_d3      --kind funding-pp $COMMON $B $FY $FX $CELL1 --funding-k 3 --hold-payments 8 --digest
run_case fundps_c1      --kind funding-ps $COMMON $B $FY $CELL1 --digest
# Задержка ноги X (C1) и эталонный движок.
run_case pair_delay2    --kind pair   $COMMON $CELL1 --delay-x 2 --digest
run_case cross_delay1   --kind cross  $COMMON $B $CELL1 --delay-x 1 --digest
run_case pair_reference --kind pair   --engine reference --panels $G/byb50 --symbols 20 --max-jobs 100000 --min-span 2000 --threads $TH $CELL1 --digest
# Независимость хеша от числа потоков (новый бинарь: 1 и 8 потоков против $TH потоков)
for th in 1 8; do
  "$BIN_N" statarb bench --kind pair --engine kernel --panels $G/byb50 --symbols 50 --max-jobs 100000 --min-span 2000 --threads $th $CELL1 --digest 2>&1 | cmp_line > "$OUT/pair_c1.N.th$th.cmp"
  if diff <(grep '^digest' "$OUT/pair_c1.N.cmp") <(grep '^digest' "$OUT/pair_c1.N.th$th.cmp") >/dev/null; then
    echo "РАВНО  pair_c1 потоков $th: $(grep '^digest' "$OUT/pair_c1.N.th$th.cmp")"
  else echo "РАЗНО  pair_c1 потоков $th"; fail=$((fail+1)); fi
done
echo "ИТОГ G6a: случаев $n, расхождений $fail"
sha256sum "$OUT"/*.N.cmp | sed "s#$OUT/##" > "$OUT/sha256-cmp.txt"
exit $fail
