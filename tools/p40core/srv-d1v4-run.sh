#!/bin/bash
# TK-522: боевой D1 (П-40 §16): orch_d1v4 (все ноги из L1 бокса, 3 варианта: through/K2/K1+60с) -> assemble с фандингом по каждому.
# Запуск на srv: bash srv-d1v4-run.sh <out> [assets,через,запятую] ; юнит p40-d1v4 (systemd-run), журнал: journalctl -u p40-d1v4.
# Умолчания — замер темпа 10.11 (jobs 8 / l1-p 6 / box-conn 16, 583 с на 10 активов по 14 сут). Переопределение окружением: JOBS, L1P, BOXCONN, NDAYS, UNIT.
# Не стартовать без слова CEO и решения Судьи по дырам L1 (а/б/в): активы с пустыми сутками L1 падают целиком.
set -e
OUT=${1:?out}; ASSETS=${2:-}
JOBS=${JOBS:-8}; L1P=${L1P:-6}; BOXCONN=${BOXCONN:-16}; NDAYS=${NDAYS:-14}; UNIT=${UNIT:-p40-d1v4}
W=/root/p40core-tk522v2; P=/root/p40core/pairs_d1_v3_nobitget.csv
FUND=/root/p40core-tk522f/fund_d1_v3.csv; VALID=/root/p40core/validity.csv.gz
DAYS=$(for i in $(seq 0 $((NDAYS - 1))); do date -u -d "2026-09-17 +$i day" +%F; done | paste -sd,)
case "$OUT" in /*) ;; *) OUT=/root/p40core/$OUT;; esac
ARGS="$P $OUT --days $DAYS --chunk $NDAYS --jobs $JOBS --l1-p $L1P --box-conn $BOXCONN --l1-trades-raw --raw-ex bitget --validity $VALID --validity-strict --variant k2:P40_K2=1 --variant k1p60:P40_SHIFT_B_MS=60000 --variant drain:P40_NOTRADE=drain"
[ -n "$ASSETS" ] && ARGS="$ARGS --assets $ASSETS"
cat > "$OUT.run.sh" <<EOF
#!/bin/bash
set -e
cd $W
python3 orch_d1v4.py $ARGS
for v in "" _k2 _k1p60 _drain; do
  case \$v in "") ctl=real;; _k2) ctl=K2;; _k1p60) ctl=K1+60000;; _drain) ctl=drain;; esac
  python3 assemble.py $OUT/d1\$v.npz $OUT/d1\$v.json --ctl \$ctl --day0 2026-09-17 --cens-s 600 \\
    --funding $FUND --pairs $P --funding-window 2026-09-17:2026-10-22 \$(cat $OUT/specs\$v.txt)
done
touch $OUT/D1V4_DONE
EOF
systemd-run --unit="$UNIT" --working-directory=$W \
  --setenv=P40_L1=1 --setenv=P40_NOTRADE=through --setenv=P40_SHM_FRAC=0.9 \
  --setenv=P40_CORE=/root/p40core/bin/p40core-nopgo-d4b5cc5c \
  bash "$OUT.run.sh"
echo "запущен $UNIT; готово = $OUT/D1V4_DONE; orch.csv — замеры"
