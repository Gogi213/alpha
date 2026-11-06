#!/usr/bin/env bash
# TK-493 w5, G17: флаг ядра --no-entry-before-from (D5(ii), аудит M3-04) на НАСТОЯЩИХ панелях формирования. Запуск на Steam Deck.
# Окно — ТОЛЬКО формирование: панели и ставки — копии prep_pform.py (сутки < 2479), сырьё для независимой сверки — копии cut_raw_form.py.
#   g17_entry_gate.sh <бинарь> <метка> [потоки=4]
#   (а) виды z с min_obs = window: флаг ничего не меняет — суточный csv, сделки, ext-out, job-day-out побайтно равны без флага и с ним
#       (первый сигнал на t = window − 1 исполняется ровно на from_min); столбец warmup_blocked = 0;
#   (б) правило funding (D2-подобное, ставки обеих ног из одного каталога): без флага часть сделок идёт через границу from_min и
#       отбрасывается (pre_window > 0); с флагом pre_window = 0, open_pre_window = 0, подавлено warmup_blocked > 0, все fill_in >= from_min
#       (check_run_ext.py --min-fill-in), --cells = склейка отдельных клеток, число сделок в окне не меньше;
#   (в) независимая сверка из сырья (check_trades.py --no-entry-before-from): сделки, счётчики (в том числе warmup_blocked), открытая позиция.
set -u
BIN=${1:?бинарь}; TAG=${2:?метка}; TH=${3:-4}
S=${GATES_SRC:-$(cd "$(dirname "$0")" && pwd)}
CHK=${CHK:-$S/check_trades.py}; CHKX=${CHKX:-$S/../check_run_ext.py}
PF=${P_ROOT:-$HOME/xdl9/pgo/pform}; FF=${F_ROOT:-$HOME/xdl9/pgo/pfund}; J=${JOBS:-$HOME/xdl9/gates/g6b}; RAWSRC=${RAW:-$HOME/xdl/out}
G=${W5_DIR:-$HOME/xdl9/w5}/g17_$TAG
mkdir -p "$G"; rm -rf "${G:?}"/*
FROM=2472; TO=2479; CUT=2479
fail=0
sha() { sha256sum < "$1" | cut -c1-16; }
nb() { local n; n=$(( $(wc -l < "$1") - 1 )); seq -s, 0 $((n-1)); }
echo "G17 $TAG: бинарь $BIN ($(sha256sum "$BIN" | cut -c1-16)), потоков $TH, окно формирования, from_day $FROM, нагрузка $(cat /proc/loadavg)"

cat > "$G/cellsW.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
w0,720,720,,2.0,0.5,inf,720,0
w1,720,720,,1.5,0.3,4.0,0,0
w2,1440,1440,,2.5,0.5,inf,1440,97
w3,720,720,1.0,2.0,0.5,3.5,480,0
w4,1440,1440,,2.0,0.5,3.0,720,200
EOF

# (а) z, min_obs = window: с флагом и без — те же выводы
za() {  # метка, вид, панели Y, панели X или -, файл заданий (from_day 2465 → 2472)
  local tag=$1 kind=$2 py=$3 px=$4 jobs=$5 D="$G/a_$1"; mkdir -p "$D"
  sed -E "s/,2465,2479\$/,$FROM,$TO/" "$jobs" > "$D/jobs.csv"
  local B=(); [ "$px" != "-" ] && B=(--panels-b "$px")
  local common=(--kind "$kind" --jobs "$D/jobs.csv" --panels "$py" "${B[@]}" --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$D/jobs.csv")" --cells "$G/cellsW.csv" --rearm --trades-ext)
  for t in off on; do
    local fl=(); [ $t = on ] && fl=(--no-entry-before-from)
    "$BIN" statarb run "${common[@]}" "${fl[@]}" --daily-out "$D/$t.daily.csv" --trades-out "$D/$t.trades.csv" --ext-out "$D/$t.ext.csv" \
      --job-day-out "$D/$t.jd.csv" --mtm-open --cells-out "$D/$t.cells.csv" --job-ext-out "$D/$t.jext.csv" > "$D/$t.log" 2>&1 || { echo "ОШИБКА run $tag $t"; tail -2 "$D/$t.log"; fail=$((fail+1)); }
  done
  # cells-out и job-ext-out: с флагом столбец warmup_blocked дописан в cells-out; job-ext-out один формат — сверка целиком
  local ok=1
  for k in daily trades ext jd; do cmp -s "$D/off.$k.csv" "$D/on.$k.csv" || { ok=0; echo "  расхождение $k"; }; done
  cmp -s "$D/off.jext.csv" "$D/on.jext.csv" || { ok=0; echo "  расхождение jext"; }
  cmp -s "$D/off.cells.csv" <(awk -F, -v OFS=, '{NF-=1; print}' "$D/on.cells.csv") || { ok=0; echo "  расхождение cells-out (без дописанного столбца)"; }
  local wb nt; wb=$(awk -F, 'NR>1{s+=$NF}END{print s+0}' "$D/on.cells.csv"); nt=$(( $(wc -l < "$D/on.trades.csv") - 1 ))
  [ "$wb" = 0 ] || ok=0
  if [ $ok = 1 ] && [ "$nt" -gt 0 ]; then echo "РАВНО  (а) $tag z min_obs=W: сделок $nt, daily/trades/ext/jd/jext/cells с флагом = без флага, warmup_blocked $wb"
  else echo "РАЗНО  (а) $tag: сделок $nt, warmup_blocked $wb"; fail=$((fail+1)); fi
}
za pair_binance pair "$PF/binance" - "$J/jobs_pair_binance.csv"
za pair_kraken  pair "$PF/kraken"  - "$J/jobs_pair_kraken.csv"
za basket_okx   basket "$PF/okx"   - "$J/jobs_basket_okx.csv"
za cross_binance_bitget cross "$PF/binance" "$PF/bitget" "$J/jobs_cross_binance_bitget.csv"

# (б) правило funding: pre_window без флага, нет его с флагом
cat > "$G/cellsF.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
f0,60,60,1.0,2.0,0.5,inf,240,0
f1,60,60,1.0,2.0,0.5,inf,720,0
EOF
zb() {  # метка, биржа
  local ex=$2 D="$G/b_$1"; mkdir -p "$D"
  sed -E "s/,2465,2479\$/,$FROM,$TO/" "$J/jobs_pair_$ex.csv" > "$D/jobs.csv"
  local common=(--kind pair --jobs "$D/jobs.csv" --panels "$PF/$ex" --funding-y "$FF/$ex" --rule funding --funding-k 3 --hold-payments 40 --min-span 1000 --threads "$TH" --trade-jobs "$(nb "$D/jobs.csv")" --trades-ext)
  for t in off on; do
    local fl=(); [ $t = on ] && fl=(--no-entry-before-from)
    "$BIN" statarb run "${common[@]}" "${fl[@]}" --cells "$G/cellsF.csv" --daily-out "$D/$t.daily.csv" --trades-out "$D/$t.trades.csv" --mtm-open \
      --cells-out "$D/$t.cells.csv" --job-ext-out "$D/$t.jext.csv" > "$D/$t.log" 2>&1 || { echo "ОШИБКА run $1 $t"; tail -2 "$D/$t.log"; fail=$((fail+1)); }
  done
  # --cells с флагом = склейка отдельных клеток (одноклеточный проход run_pair)
  : > "$D/s.daily.body"; : > "$D/s.trades.body"
  tail -n +2 "$G/cellsF.csv" | while IFS=, read -r cell window min_obs beta entry exit stop hold refresh; do
    "$BIN" statarb run "${common[@]}" --no-entry-before-from --cell "$cell" --window "$window" --min-obs "$min_obs" --beta-fixed "$beta" --entry-z "$entry" --exit-z "$exit" \
      --stop-z "$stop" --max-hold "$hold" --refresh "$refresh" --daily-out "$D/s.$cell.daily.csv" --trades-out "$D/s.$cell.trades.csv" > "$D/s.$cell.log" 2>&1 || echo "ОШИБКА --cell $cell"
    tail -n +2 "$D/s.$cell.daily.csv" >> "$D/s.daily.body"; tail -n +2 "$D/s.$cell.trades.csv" >> "$D/s.trades.body"
  done
  local ok=1
  cmp -s <(tail -n +2 "$D/on.daily.csv") "$D/s.daily.body" || { ok=0; echo "  --cells != склейка (daily)"; }
  cmp -s <(tail -n +2 "$D/on.trades.csv") "$D/s.trades.body" || { ok=0; echo "  --cells != склейка (trades)"; }
  # счётчики: сумма по клеткам
  read -r tr_off pre_off wb_off < <(awk -F, 'NR>1{t+=$6;p+=$7}END{print t+0,p+0,0}' "$D/off.cells.csv")
  read -r tr_on pre_on opre_on < <(awk -F, 'NR>1{t+=$6;p+=$7;o+=$15}END{print t+0,p+0,o+0}' "$D/on.cells.csv")
  wb_on=$(awk -F, 'NR>1{s+=$NF}END{print s+0}' "$D/on.cells.csv")
  [ "$pre_on" = 0 ] && [ "$opre_on" = 0 ] && [ "$wb_on" -gt 0 ] && [ "$pre_off" -gt 0 ] || ok=0
  [ $(( tr_on - pre_on )) -ge $(( tr_off - pre_off )) ] || ok=0
  # все входы с флагом — не раньше границы
  python3 "$CHKX" --trades "$D/on.trades.csv" --panels-y "$PF/$ex" --panels-x "$PF/$ex" --funding-y "$FF/$ex" --funding-x "$FF/$ex" --n 3 --min-fill-in $((FROM*1440)) > "$D/chkx.log" 2>&1 || { ok=0; tail -3 "$D/chkx.log"; }
  if [ $ok = 1 ]; then echo "РАВНО  (б) $1 funding k=3 h=40: без флага сделок $tr_off (до окна отброшено $pre_off), с флагом $tr_on (отброшено $pre_on, открыто до окна $opre_on), подавлено минут сигнала $wb_on; --cells = склейка клеток; входов раньше границы 0"
  else echo "РАЗНО  (б) $1: без флага сделок $tr_off (pre $pre_off), с флагом $tr_on (pre $pre_on, open_pre $opre_on), warmup_blocked $wb_on"; fail=$((fail+1)); fi
}
zb funding_binance binance
zb funding_bybit bybit

# (в) независимая сверка из сырья (копии формирования): сделки, счётчики, открытая позиция, warmup_blocked
RF="$G/rawform"
declare -A BTC=( [binance]=BTCUSDT [bybit]=BTCUSDT ) ETH=( [binance]=ETHUSDT [bybit]=ETHUSDT ) SOL=( [binance]=SOLUSDT [bybit]=SOLUSDT )
specs=(); for ex in binance bybit; do for s in "${BTC[$ex]}" "${ETH[$ex]}" "${SOL[$ex]}"; do specs+=("$ex:$s"); done; done
python3 "$S/cut_raw_form.py" "$RAWSRC" "$RF" $CUT "${specs[@]}" > "$G/cut_raw.log" 2>&1 || { echo "ОШИБКА cut_raw_form"; tail -3 "$G/cut_raw.log"; fail=$((fail+1)); }
tot=0; bad=0; blocked=0
for rule in funding z; do
  for ex in binance bybit; do
    for alt in ETH SOL; do
      if [ $alt = ETH ]; then Y=${ETH[$ex]}; else Y=${SOL[$ex]}; fi
      X=${BTC[$ex]}; D="$G/c_${rule}_${ex}_$alt"; mkdir -p "$D"
      printf 'y,x,from_day,to_day\n%s,%s,%s,%s\n' "$Y" "$X" "$FROM" "$TO" > "$D/jobs.csv"
      if [ $rule = funding ]; then
        RUN=(--rule funding --funding-k 3 --hold-payments 40 --window 60 --min-obs 60 --beta-fixed 1.0 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 240 --refresh 0)
        CK=(--rule funding --funding-k 3 --hold-payments 40 --window 60 --min-obs 60 --beta-fixed 1.0 --max-hold 240)
      else
        # min_obs < window: вход в разогреве возможен, флаг его подавляет (счётчик warmup_blocked > 0)
        RUN=(--window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z 3.5 --max-hold 720 --refresh 0 --rearm)
        CK=(--rule z --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z 3.5 --max-hold 720 --rearm)
      fi
      "$BIN" statarb run --kind pair --cell c "${RUN[@]}" --no-entry-before-from --jobs "$D/jobs.csv" --panels "$PF/$ex" --funding-y "$FF/$ex" --min-span 1000 --threads 2 \
        --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs 0 --trades-ext --ext-out "$D/ext.csv" --mtm-open --cells-out "$D/cells.csv" \
        --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА run $rule $ex $alt"; tail -2 "$D/run.log"; }
      python3 "$CHK" --root "$RF" --y-ex $ex --y "$Y" --x-ex $ex --x "$X" --from-day $FROM --to-day $TO "${CK[@]}" --funding --no-entry-before-from \
        --trades "$D/trades.csv" --cells-out "$D/cells.csv" --open-out "$D/open.csv" --show 0 > "$D/check.log" 2>&1
      rc=$?; tot=$((tot+1)); [ $rc -ne 0 ] && bad=$((bad+1))
      wb=$(awk -F, 'NR>1{s+=$NF}END{print s+0}' "$D/cells.csv"); blocked=$((blocked+wb))
      echo "== (в) $rule $ex $alt: $(grep -o 'сделок в csv [0-9]*, пересчитано [0-9]* (до окна отброшено [0-9]*)' "$D/check.log"), расхождений $(grep -o 'расхождений [0-9]*' "$D/check.log" | head -1 | cut -d' ' -f2), warmup_blocked run $wb, rc=$rc"
      [ $rc -ne 0 ] && cat "$D/check.log"
    done
  done
done
if [ $bad = 0 ] && [ $blocked -gt 0 ]; then echo "РАВНО  (в) независимая сверка из сырья: заданий $tot, расхождений 0, подавлено сигналов (сумма) $blocked"
else echo "РАЗНО  (в) заданий $tot, с расхождениями $bad, подавлено $blocked"; fail=$((fail+1)); fi
echo "ИТОГ G17 $TAG: расхождений $fail"
exit $fail
