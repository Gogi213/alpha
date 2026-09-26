#!/usr/bin/env bash
# Гейт Р3 (T-15/T-17, условия Судьи `docs/research/reviews/backtest-optimization-2026-09-26.md`): варианты одного
# сигнала — ОДНИМ вызовом `bounce-grid` (формы складываются произведением) вместо вызова на вариант. На каждых сутках
# (с переносом через полночь, несколько `--set` — память кругов включена) — четыре отдельных вызова (как H10/H7/пара/база
# П-02: вход {лестница, фронтран} × стоп {pct2, before}) и один слитый при `--threads 2` и `--threads 1`. Проверки:
#   1) строки `rounds.csv`/`forms.csv` каждой формы в слитом = строки отдельного вызова (побайтно, по колонке `form`);
#   2) число строк на форму — печатается;
#   3) слитый при `--threads 1` = при `--threads 2` целиком (шапки — без поля `threads=`);
#   4) `portfolio-sim.py` по каждой форме: отдельный = слитый (вывод JSON побайтно);
#   5) время: сумма отдельных против слитого — выигрыш Р3 замером.
#   BIN=bin/alpha-<хеш> gate-merge.sh <дом>:<сутки> ...     (дом — каталог эпохи; по одному bounce-grid за раз)
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
BIN="${BIN:?бинарник}"
OUT="${OUT:-$(mktemp -d)}"
mkdir -p "$OUT" || exit 1
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
COMMON="--signal approach --queue-model prob:3 $RTT --regime-from study/regime --order-usd 500 --carry-root root \
  --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --take-form tr1x1 --deadline-secs 14400 \
  --exit-form none --set t-bid-age-45:age=2700,side=bid --set t-bid-btc1h-q1:age=2700,side=bid,btc1h_max=-21.17 \
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55"
SEP="ladder3x2..20w2:pct2 ladder3x2..20w2:before single@fr:pct2 single@fr:before"
fail=0
declare -A secs=()

run() {  # $1 дом, $2 сутки, $3 каталог прогона (относительно дома), $4.. формы и потоки
  local home=$1 day=$2 run=$3; shift 3
  local t0; t0=$(date +%s)
  # shellcheck disable=SC2086
  (cd "$home" && nice -n 5 "$A/$BIN" lob bounce-grid --root "study/root-$day" --touches-from study/approaches/D20 \
     $COMMON "$@" --out-dir "$run/$day" > "$run/$day.log" 2>&1)
  local rc=$?
  secs[$run]=$(( ${secs[$run]:-0} + $(date +%s) - t0 ))
  [ "$rc" = 0 ] || { echo "FAIL $run $day: код $rc — $(tail -1 "$run/$day.log" | cut -c1-150)"; fail=1; }
}

rows() {  # $1 файл csv, $2 форма → строки этой формы (без `#` и заголовка)
  awk -F, -v f="$2" '!/^#/ && NR > 0 && $3 == f' "$1"
}

for spec in "$@"; do
  home=${spec%%:*}; day=${spec##*:}
  case "$home" in /*) ;; *) home="$A/$home" ;; esac
  [ -d "$home/study/root-$day" ] || { echo "FAIL $spec: нет корня"; fail=1; continue; }
  for s in $SEP; do
    e=${s%%:*}; st=${s##*:}
    run "$home" "$day" "$OUT/sep-$e-$st" --entry-form "$e" --stop-form "$st" --threads 2
  done
  run "$home" "$day" "$OUT/merged" --entry-form ladder3x2..20w2 --entry-form single@fr --stop-form pct2 \
    --stop-form before --threads 2
  run "$home" "$day" "$OUT/merged-t1" --entry-form ladder3x2..20w2 --entry-form single@fr --stop-form pct2 \
    --stop-form before --threads 1
  [ "$fail" = 0 ] || continue
  # 3) --threads 1 = 2 целиком, кроме поля threads= в шапках.
  if diff -r <(cd "$OUT/merged/$day" && find . -type f | sort | xargs sed 's/threads=[0-9]*//') \
             <(cd "$OUT/merged-t1/$day" && find . -type f | sort | xargs sed 's/threads=[0-9]*//') > /dev/null; then
    echo "OK   $day: слитый --threads 1 = --threads 2 (все файлы, шапки без threads=)"
  else
    echo "DIFF $day: слитый --threads 1 ≠ --threads 2"; fail=1
  fi
  # 1–2) строки каждой формы.
  for s in $SEP; do
    e=${s%%:*}; st=${s##*:}
    sepdir="$OUT/sep-$e-$st/$day"
    for setdir in "$sepdir"/*/; do
      set_name=$(basename "$setdir")
      for f in rounds.csv forms.csv; do
        forms=$(awk -F, '!/^#/ && $3 != "form" {print $3}' "$setdir/$f" | sort -u)
        for form in $forms; do
          a=$(rows "$setdir/$f" "$form" | md5sum | cut -c1-12)
          b=$(rows "$OUT/merged/$day/$set_name/$f" "$form" | md5sum | cut -c1-12)
          n=$(rows "$setdir/$f" "$form" | wc -l)
          if [ "$a" = "$b" ]; then echo "OK   $day $set_name $f $form: строк $n"; else echo "DIFF $day $set_name $f $form"; fail=1; fi
        done
      done
    done
  done
done

# 4) portfolio-sim по формам главного набора: отдельный прогон = слитый. PSIM — доп. аргументы (напр.
# `--klines $HOME/alpha/study/klines`); пусто — шаг пропускается.
if [ "$fail" = 0 ] && [ -n "${PSIM:-}" ]; then
  for s in $SEP; do
    e=${s%%:*}; st=${s##*:}
    form=$(awk -F, '!/^#/ && $3 != "form" {print $3; exit}' "$(ls -d "$OUT/sep-$e-$st"/*/t-bid-btc4h-q1 | head -1)/forms.csv")
    [ -n "$form" ] || continue
    for run in "sep-$e-$st" merged; do
      # shellcheck disable=SC2086
      python3 "$A/bin/portfolio-sim.py" --epoch "g=$OUT:$run" --variant "v=t-bid-btc4h-q1/$form" \
        --deposit-usd 2500 --position-usd 500 $PSIM --json "$OUT/psim-$run-$form.json" > "$OUT/psim-$run-$form.log" 2>&1 \
        || echo "FAIL portfolio-sim $run $form: $(tail -1 "$OUT/psim-$run-$form.log" | cut -c1-150)"
    done
    if cmp -s "$OUT/psim-sep-$e-$st-$form.json" "$OUT/psim-merged-$form.json"; then
      echo "OK   portfolio-sim $form: отдельный = слитый"
    else
      echo "DIFF portfolio-sim $form"; fail=1
    fi
  done
fi

sep_total=0; for s in $SEP; do e=${s%%:*}; st=${s##*:}; sep_total=$(( sep_total + ${secs[$OUT/sep-$e-$st]:-0} )); done
echo "TIME отдельные (4 вызова) ${sep_total} с · слитый ${secs[$OUT/merged]:-0} с (--threads 2) · слитый ${secs[$OUT/merged-t1]:-0} с (--threads 1)"
[ "$fail" = 0 ] && echo "ГЕЙТ Р3: всё совпало" || echo "ГЕЙТ Р3: ЕСТЬ РАСХОЖДЕНИЯ"
exit "$fail"
