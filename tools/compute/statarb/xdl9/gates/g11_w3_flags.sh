#!/usr/bin/env bash
# TK-493 w3, G11: новые флаги ядра (--rearm, --trades-ext, --ext-out, --job-day-out) на НАСТОЯЩИХ панелях. Запуск на Steam Deck.
# Окно — ФОРМИРОВАНИЕ (сутки 2465..2478): торговое окно для технических гейтов не открываем.
#   (1) новые флаги без --rearm не меняют старые выводы: суточный csv и сделки (без двух дописанных столбцов) побайтно равны
#       выводу эталонного бинаря (BIN_O, по умолчанию 02c6a0df);
#   (2) с --rearm: --cells (многоклеточный проход) = склейка отдельных клеток (суточный csv, сделки, --ext-out, --job-day-out);
#   (3) --job-day-out: сумма по заданиям (в порядке номера, от нуля) = суточный csv клетки побитно, без и с --rearm;
#   (4) сводка влияния --rearm по клеткам: сделок, выходы по причинам, подавлено сигналов, чистый bps.
# Независимый пересчёт из сырья (check_trades.py, --rearm) — g11b.
#   g11_w3_flags.sh [потоки]
set -u
BIN_N=${BIN_N:-$HOME/xdl9/bin/statarb-xdl9-6108b0bf}
BIN_O=${BIN_O:-$HOME/xdl9/bin/statarb-xdl9-02c6a0df}
P=${P_DIR:-$HOME/xdl9/panels}   # TK-493 w5: P_DIR=<pform> — копии панелей, обрезанные до суток формирования
J=${G11_JOBS:-$HOME/xdl9/gates/g6b}
G=${G11_DIR:-$HOME/xdl9/gates/g11}
TH=${1:-4}
mkdir -p "$G"; rm -rf "${G:?}"/*
cat > "$G/cells.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
c0,720,360,,2.0,0.5,inf,720,0
c1,720,360,,1.5,0.3,4.0,0,0
c2,1440,720,,2.5,0.5,inf,1440,97
c3,720,360,1.0,2.0,0.5,3.5,480,0
c4,1440,720,,2.0,0.5,3.0,720,200
EOF
fail=0
nb() { local n; n=$(( $(wc -l < "$1") - 1 )); seq -s, 0 $((n-1)); }
strip2() { awk -F, -v OFS=, '{NF-=2; print}' "$1"; }   # убрать два последних столбца (exit_reason,beta_in)
sha() { sha256sum < "$1" | cut -c1-16; }

# python-проверка (3): сумма --job-day-out по заданиям = суточный csv (точное равенство f64 из текста)
cat > "$G/jdsum.py" <<'PY'
import csv, sys
jd, daily = sys.argv[1], sys.argv[2]
sums = {}
for r in csv.DictReader(open(jd, encoding="utf-8")):
    s = sums.setdefault((r["cell"], int(r["day"])), [0.0, 0.0, 0.0, 0.0, 0.0])
    for i, k in enumerate(("net_bps", "gross_bps", "fee_bps", "funding_bps", "trades")):
        s[i] += float(r[k])
rows = list(csv.DictReader(open(daily, encoding="utf-8")))
bad = 0
for r in rows:
    s = sums.get((r["cell"], int(r["day"])))
    if s is None or any(s[i] != float(r[k]) for i, k in enumerate(("net_bps", "gross_bps", "fee_bps", "funding_bps", "trades"))):
        bad += 1
withtr = sum(1 for v in sums.values() if v[4] > 0)
act = sum(1 for r in csv.DictReader(open(jd, encoding="utf-8")) if r["active"] == "1")
tot = sum(1 for _ in csv.DictReader(open(jd, encoding="utf-8")))
print(f"{len(rows)} суток клеток в daily, расхождений с суммой job-day {bad}; суток с торговлей в job-day {withtr}; строк job-day {tot}, активных {act}")
sys.exit(1 if bad or withtr != len(rows) else 0)
PY

run_set() {  # метка, вид, панели Y, панели X (или -), файл заданий
  local tag=$1 kind=$2 py=$3 px=$4 jobs=$5
  local B=(); [ "$px" != "-" ] && B=(--panels-b "$px")
  local TJ; TJ=$(nb "$jobs"); local D="$G/$tag"; mkdir -p "$D"
  local common=(--kind "$kind" --jobs "$jobs" --panels "$py" "${B[@]}" --min-span 1000 --threads "$TH" --trade-jobs "$TJ")
  local ext=(--trades-ext)
  # (1) эталонный бинарь против нового без --rearm, все новые выводы включены
  "$BIN_O" statarb run "${common[@]}" --cells "$G/cells.csv" --daily-out "$D/o.daily.csv" --trades-out "$D/o.trades.csv" > "$D/o.log" 2>&1 || { echo "ОШИБКА старый бинарь $tag"; tail -2 "$D/o.log"; fail=$((fail+1)); }
  "$BIN_N" statarb run "${common[@]}" "${ext[@]}" --cells "$G/cells.csv" --daily-out "$D/n.daily.csv" --trades-out "$D/n.trades.csv" \
    --ext-out "$D/n.ext.csv" --job-day-out "$D/n.jd.csv" > "$D/n.log" 2>&1 || { echo "ОШИБКА новый бинарь $tag"; tail -2 "$D/n.log"; fail=$((fail+1)); }
  local nt; nt=$(( $(wc -l < "$D/o.trades.csv") - 1 ))
  if [ "$(sha "$D/o.daily.csv")" = "$(sha "$D/n.daily.csv")" ] && [ "$(strip2 "$D/n.trades.csv" | sha256sum | cut -c1-16)" = "$(sha "$D/o.trades.csv")" ] && [ "$nt" -gt 0 ]; then
    echo "РАВНО  (1) $tag без --rearm: сделок $nt, суточный csv и сделки (без 2 новых столбцов) = $(sha "$D/o.daily.csv") / $(sha "$D/o.trades.csv")"
  else echo "РАЗНО  (1) $tag без --rearm"; fail=$((fail+1)); fi
  python3 "$G/jdsum.py" "$D/n.jd.csv" "$D/n.daily.csv" > "$D/n.jdsum.log" 2>&1 && echo "РАВНО  (3) $tag job-day без --rearm: $(cat "$D/n.jdsum.log")" || { echo "РАЗНО  (3) $tag job-day без --rearm: $(cat "$D/n.jdsum.log")"; fail=$((fail+1)); }
  # (2) --rearm: --cells против склейки отдельных клеток
  "$BIN_N" statarb run "${common[@]}" "${ext[@]}" --rearm --cells "$G/cells.csv" --daily-out "$D/r.daily.csv" --trades-out "$D/r.trades.csv" \
    --ext-out "$D/r.ext.csv" --job-day-out "$D/r.jd.csv" > "$D/r.log" 2>&1 || { echo "ОШИБКА --rearm --cells $tag"; tail -2 "$D/r.log"; fail=$((fail+1)); }
  : > "$D/s.daily.body"; : > "$D/s.trades.body"; : > "$D/s.ext.body"; : > "$D/s.jd.body"
  tail -n +2 "$G/cells.csv" | while IFS=, read -r cell window min_obs beta entry exit stop hold refresh; do
    local bf=(); [ -n "$beta" ] && bf=(--beta-fixed "$beta")
    "$BIN_N" statarb run "${common[@]}" "${ext[@]}" --rearm --cell "$cell" --window "$window" --min-obs "$min_obs" "${bf[@]}" --entry-z "$entry" --exit-z "$exit" \
      --stop-z "$stop" --max-hold "$hold" --refresh "$refresh" --daily-out "$D/s.$cell.daily.csv" --trades-out "$D/s.$cell.trades.csv" \
      --ext-out "$D/s.$cell.ext.csv" --job-day-out "$D/s.$cell.jd.csv" > "$D/s.$cell.log" 2>&1 || echo "ОШИБКА --rearm --cell $cell $tag"
    for k in daily trades ext jd; do tail -n +2 "$D/s.$cell.$k.csv" >> "$D/s.$k.body"; done
  done
  local ok=1
  for k in daily trades ext jd; do
    if [ "$k" = ext ]; then   # в ext-out одна строка на клетку: цифры f64 одинаковы побитно (текст), колонки те же
      cmp -s <(tail -n +2 "$D/r.$k.csv") "$D/s.$k.body" || { ok=0; echo "  расхождение $k: --cells $(sha "$D/r.$k.csv") против склейки"; }
    else
      cmp -s <(tail -n +2 "$D/r.$k.csv") "$D/s.$k.body" || { ok=0; echo "  расхождение $k"; }
    fi
  done
  local rt; rt=$(( $(wc -l < "$D/r.trades.csv") - 1 ))
  [ $ok = 1 ] && echo "РАВНО  (2) $tag --rearm: --cells = склейка 5 клеток (суточный, сделки $rt, ext-out, job-day)" || { echo "РАЗНО  (2) $tag --rearm"; fail=$((fail+1)); }
  python3 "$G/jdsum.py" "$D/r.jd.csv" "$D/r.daily.csv" > "$D/r.jdsum.log" 2>&1 && echo "РАВНО  (3) $tag job-day с --rearm: $(cat "$D/r.jdsum.log")" || { echo "РАЗНО  (3) $tag job-day с --rearm: $(cat "$D/r.jdsum.log")"; fail=$((fail+1)); }
  # (4) сводка влияния
  python3 - "$D/n.ext.csv" "$D/r.ext.csv" "$tag" <<'PY'
import csv, sys
off = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
on = list(csv.DictReader(open(sys.argv[2], encoding="utf-8")))
for a, b in zip(off, on):
    f = lambda r, k: float(r[k])
    print(f"  (4) {sys.argv[3]:<22} {a['cell']}: сделок {int(f(a,'trades_n'))} -> {int(f(b,'trades_n'))} (стоп {int(f(a,'exit_stop'))} -> {int(f(b,'exit_stop'))}, срок {int(f(a,'exit_timeout'))} -> {int(f(b,'exit_timeout'))}, "
          f"правило {int(f(a,'exit_rule'))} -> {int(f(b,'exit_rule'))}), подавлено сигналов {int(f(b,'rearm_blocked'))}; net на сделку, bps Y: {f(a,'net_y_mean_bps'):.2f} -> {f(b,'net_y_mean_bps'):.2f}; "
          f"z определён {f(a,'z_def_share'):.4f}, в позиции z=NaN {f(a,'pos_z_nan_share'):.4f}, без права входа из-за окна {int(f(a,'noentry_window_minutes'))} мин; "
          f"β<0 {f(a,'beta_neg_share'):.3f}, β p01/p50/p99 {f(a,'beta_p01'):.2f}/{f(a,'beta_p50'):.2f}/{f(a,'beta_p99'):.2f}")
PY
}

run_set pair_binance pair "$P/binance" - "$J/jobs_pair_binance.csv"
run_set pair_bitget  pair "$P/bitget"  - "$J/jobs_pair_bitget.csv"
run_set pair_kraken  pair "$P/kraken"  - "$J/jobs_pair_kraken.csv"
run_set basket_okx   basket "$P/okx"   - "$J/jobs_basket_okx.csv"
run_set cross_binance_bitget cross "$P/binance" "$P/bitget" "$J/jobs_cross_binance_bitget.csv"
echo "ИТОГ G11: расхождений $fail"
exit $fail
