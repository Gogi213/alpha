#!/usr/bin/env bash
# TK-493 F6, G6 (часть б): новый бинарь против tk486@60e61cea — `statarb run`, фандинг выключен (в tk486 E-010 не исправлен).
# Запуск на Steam Deck. Для каждого набора (вид × биржа):
#   (1) каждая клетка отдельным прогоном `--cell` — суточный csv и сделки; (2) все клетки одним `--cells`;
#   сверяются sha256: tk486 --cell == новый --cell; tk486 --cells == новый --cells; --cells == склейка --cell (в каждом бинаре).
# Окно — ФОРМИРОВАНИЕ (сутки 2465..2478 = 01.10..14.10): торговое окно 15.10..04.11 для технических гейтов не открываем.
#   g6b_run_vs_tk486.sh [потоки]
set -u
BIN_O=$HOME/xdl9/bin/statarb-tk486-60e61cea
BIN_N=${BIN_N:-$HOME/xdl9/bin/statarb-xdl9-02c6a0df}   # TK-493 w3: BIN_N / G6B_DIR переопределяют бинарь и каталог
P=$HOME/xdl9/panels
G=${G6B_DIR:-$HOME/xdl9/gates/g6b}
TH=${1:-4}
FROM=2465; TO=2479
mkdir -p "$G"; rm -rf "${G:?}"/*

cat > "$G/cells.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
c0,720,360,,2.0,0.5,inf,720,0
c1,720,360,,1.5,0.3,4.0,0,0
c2,1440,720,,2.5,0.5,inf,1440,97
c3,720,360,1.0,2.0,0.5,3.5,480,0
c4,1440,720,,2.0,0.5,3.0,720,200
EOF

# Наборы заданий из манифеста панелей (14 символов на биржу: 10 самых полных + 4 самых дырявых с достаточными данными).
python3 - "$P/manifest.csv" "$G" "$FROM" "$TO" <<'PY'
import csv, itertools, os, sys
man, g, fr, to = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
byex = {}
for r in csv.DictReader(open(man, encoding="utf-8")):
    if r["panel"] == "1" and r["panel_minutes"] and int(r["panel_valid"]) >= 5000:
        byex.setdefault(r["exchange"], []).append(r)
pick = {}
for ex, rows in byex.items():
    rows.sort(key=lambda r: (-int(r["panel_valid"]), r["symbol"]))
    top = rows[:10]
    rest = [r for r in rows[10:] if int(r["panel_valid"]) >= 20000]
    rest.sort(key=lambda r: (-(int(r["panel_nan"]) / max(1, int(r["panel_minutes"]))), r["symbol"]))
    syms = [r["symbol"] for r in top] + [r["symbol"] for r in rest[:4]]
    pick[ex] = sorted(set(syms))
    with open(os.path.join(g, f"jobs_pair_{ex}.csv"), "w") as w:
        w.write("y,x,from_day,to_day\n")
        for a, b in itertools.combinations(pick[ex], 2):
            w.write(f"{a},{b},{fr},{to}\n")
    with open(os.path.join(g, f"jobs_basket_{ex}.csv"), "w") as w:
        w.write("y,x,from_day,to_day\n")
        s = pick[ex]
        for i in range(len(s)):
            w.write(f"{s[i]},{'+'.join(s[(i + d) % len(s)] for d in (1, 2, 3))},{fr},{to}\n")
for b in ("bybit", "aster", "bitget"):
    names = sorted(set(r["symbol"] for r in byex[b]) & set(r["symbol"] for r in byex["binance"]))
    # 12 общих символов: те, что в первой десятке Binance, если есть на второй бирже, дальше по имени
    first = [s for s in pick["binance"] if s in names][:12]
    first += [s for s in names if s not in first][: 12 - len(first)]
    with open(os.path.join(g, f"jobs_cross_binance_{b}.csv"), "w") as w:
        w.write("y,x,from_day,to_day\n")
        for s in first:
            w.write(f"{s},{s},{fr},{to}\n")
print({ex: len(v) for ex, v in pick.items()})
PY

NC=$(( $(wc -l < "$G/cells.csv") - 1 ))
fail=0; total=0
nb() { local n; n=$(( $(wc -l < "$1") - 1 )); seq -s, 0 $((n-1)); }

check_set() {  # метка, вид, панели Y, панели X (или -), файл заданий
  local tag=$1 kind=$2 py=$3 px=$4 jobs=$5
  local B=(); [ "$px" != "-" ] && B=(--panels-b "$px")
  local TJ; TJ=$(nb "$jobs")
  local D="$G/$tag"; mkdir -p "$D"
  local common=(--kind "$kind" --jobs "$jobs" --panels "$py" "${B[@]}" --min-span 1000 --threads "$TH" --trade-jobs "$TJ")
  # (2) --cells в обоих бинарях
  for t in O N; do
    local bin=$BIN_O; [ $t = N ] && bin=$BIN_N
    "$bin" statarb run "${common[@]}" --cells "$G/cells.csv" --daily-out "$D/cells.$t.daily.csv" --trades-out "$D/cells.$t.trades.csv" > "$D/cells.$t.log" 2>&1 || { echo "ОШИБКА run --cells $t $tag"; cat "$D/cells.$t.log" | tail -3; }
  done
  # (1) одиночные клетки в обоих бинарях
  for t in O N; do
    local bin=$BIN_O; [ $t = N ] && bin=$BIN_N
    : > "$D/single.$t.daily.body"; : > "$D/single.$t.trades.body"
    local i=0
    tail -n +2 "$G/cells.csv" | while IFS=, read -r cell window min_obs beta entry exit stop hold refresh; do
      local bf=(); [ -n "$beta" ] && bf=(--beta-fixed "$beta")
      "$bin" statarb run "${common[@]}" --cell "$cell" --window "$window" --min-obs "$min_obs" "${bf[@]}" --entry-z "$entry" --exit-z "$exit" --stop-z "$stop" --max-hold "$hold" --refresh "$refresh" \
        --daily-out "$D/s.$t.$cell.daily.csv" --trades-out "$D/s.$t.$cell.trades.csv" > "$D/s.$t.$cell.log" 2>&1 || echo "ОШИБКА run --cell $cell $t $tag"
      # шапка — из первого, тело — склейка в порядке клеток файла
      if [ ! -s "$D/single.$t.daily.hdr" ]; then head -1 "$D/s.$t.$cell.daily.csv" > "$D/single.$t.daily.hdr"; head -1 "$D/s.$t.$cell.trades.csv" > "$D/single.$t.trades.hdr"; fi
      tail -n +2 "$D/s.$t.$cell.daily.csv" >> "$D/single.$t.daily.body"
      tail -n +2 "$D/s.$t.$cell.trades.csv" >> "$D/single.$t.trades.body"
    done
    cat "$D/single.$t.daily.hdr" "$D/single.$t.daily.body" > "$D/single.$t.daily.csv"
    cat "$D/single.$t.trades.hdr" "$D/single.$t.trades.body" > "$D/single.$t.trades.csv"
  done
  local nt nd; nt=$(( $(wc -l < "$D/cells.N.trades.csv") - 1 )); nd=$(( $(wc -l < "$D/cells.N.daily.csv") - 1 ))
  local ok=1 detail=""
  for k in daily trades; do
    local h_on h_nn h_os h_ns h_oc h_nc
    h_oc=$(sha256sum < "$D/cells.O.$k.csv" | cut -c1-16); h_nc=$(sha256sum < "$D/cells.N.$k.csv" | cut -c1-16)
    h_os=$(sha256sum < "$D/single.O.$k.csv" | cut -c1-16); h_ns=$(sha256sum < "$D/single.N.$k.csv" | cut -c1-16)
    detail="$detail $k[tk486 cells=$h_oc single=$h_os | новый cells=$h_nc single=$h_ns]"
    [ "$h_oc" = "$h_nc" ] && [ "$h_os" = "$h_ns" ] && [ "$h_nc" = "$h_ns" ] && [ "$h_oc" = "$h_os" ] || ok=0
  done
  total=$((total+1))
  if [ $ok = 1 ] && [ "$nt" -gt 0 ]; then echo "РАВНО  $tag: сделок $nt, строк суточного $nd;$detail"
  else echo "РАЗНО  $tag: сделок $nt, строк суточного $nd;$detail"; fail=$((fail+1)); fi
}

for ex in binance bybit okx bingx bitget kraken aster lighter; do
  check_set "pair_$ex"   pair   "$P/$ex" - "$G/jobs_pair_$ex.csv"
  check_set "basket_$ex" basket "$P/$ex" - "$G/jobs_basket_$ex.csv"
done
for b in bybit aster bitget; do
  check_set "cross_binance_$b" cross "$P/binance" "$P/$b" "$G/jobs_cross_binance_$b.csv"
done
echo "ИТОГ G6b: наборов $total (клеток в наборе $NC), расхождений $fail"
( cd "$G" && sha256sum */cells.N.*.csv */single.N.*.csv > sha256-new.txt; sha256sum */cells.O.*.csv */single.O.*.csv > sha256-tk486.txt )
exit $fail
