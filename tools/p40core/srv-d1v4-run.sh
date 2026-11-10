#!/bin/bash
# TK-522: боевой D1 (П-40 §16): orch_d1v4 (все ноги из L1 бокса, 3 варианта: through/K2/K1+60с) -> assemble с фандингом по каждому.
# Запуск на srv: bash srv-d1v4-run.sh <out> [assets,через,запятую] ; юнит p40-d1v4 (systemd-run), журнал: journalctl -u p40-d1v4.
# Умолчания — замер темпа 10.11 (jobs 8 / l1-p 6 / box-conn 16, 583 с на 10 активов по 14 сут). Переопределение окружением: JOBS, L1P, BOXCONN, NDAYS, UNIT.
# CEO 10.11 13:35: пуск по готовому (Bitget вне плана D1 — pairs nobitget; --raw-ex убран: с --validity-l1 даёт RuntimeError); юнит Restart=on-failure, RESTART_S между попытками: orch пропускает задания с DONE, упавшие (L1 ещё не достроен, TK-521) повторяет = дельта сама; assemble и D1V4_DONE — когда orch вышел с rc 0.
# Перезапуск с возрастом на t0 (Судья §2 п. 2): systemctl stop p40-d1v4; AGE=1 W=/root/p40core-tk522h CORE=/root/p40core/bin/p40core-nopgo-age bash srv-d1v4-run.sh <тот же out> (готовые без AGE пересчитываются, остальное — дельта).
# Судья 10.11: дыры L1 класса 3 -> негодные часы (validity по L1, --validity-l1, без G0-таблицы); после DONE: python3 validity_l1_cov.py <пары> <out>/validity_l1.csv <сутки> (100 %). Не стартовать без слова CEO и достройки L1 (TK-521).
set -e
OUT=${1:?out}; ASSETS=${2:-}
JOBS=${JOBS:-8}; L1P=${L1P:-6}; BOXCONN=${BOXCONN:-16}; NDAYS=${NDAYS:-14}; UNIT=${UNIT:-p40-d1v4}; RESTART_S=${RESTART_S:-300}
W=${W:-/root/p40core-tk522g}; AGE=${AGE:-0}; P=/root/p40core/pairs_d1_v3_nobitget.csv
FUND=/root/p40core-tk522f/fund_d1_v3.csv
DAYS=$(for i in $(seq 0 $((NDAYS - 1))); do date -u -d "2026-09-17 +$i day" +%F; done | paste -sd,)
case "$OUT" in /*) ;; *) OUT=/root/p40core/$OUT;; esac
AGEARG=""; STALE=""; [ "$AGE" = 1 ] && { AGEARG="--age"; STALE="--stale-smax $OUT/smax_l1.csv"; }   # AGE=1 (TK-522, Судья 10.11 §2 п. 2): возраст лучших цен на t0 -> assemble помечает stale; W=дерево с ядром `age`
ARGS="$P $OUT $AGEARG --days $DAYS --chunk $NDAYS --jobs $JOBS --l1-p $L1P --box-conn $BOXCONN --l1-trades-raw --validity-l1 --variant k2:P40_K2=1 --variant k1p60:P40_SHIFT_B_MS=60000 --variant drain:P40_NOTRADE=drain"
[ -n "$ASSETS" ] && ARGS="$ARGS --assets $ASSETS"
cat > "$OUT.run.sh" <<EOF
#!/bin/bash
set -e
cd $W
python3 orch.py $ARGS
for f in $OUT/specs*.txt $OUT/validity_l1.csv $OUT/l1_gaps.csv $OUT/l1_nodata.csv; do   # упавшее задание при повторе дописывает те же строки: убрать дубли, порядок первых вхождений
  if [ -f "\$f" ]; then awk '!s[\$0]++' "\$f" > "\$f.u" && mv "\$f.u" "\$f"; fi
done
[ "$AGE" = 1 ] && python3 smax_l1.py $OUT/validity_l1.csv $OUT/smax_l1.csv
for v in "" _k2 _k1p60 _drain; do
  case \$v in "") ctl=real;; _k2) ctl=K2;; _k1p60) ctl=K1+60000;; _drain) ctl=drain;; esac
  python3 assemble.py $OUT/d1\$v.npz $OUT/d1\$v.json --ctl \$ctl --day0 2026-09-17 --cens-s 600 \\
    $STALE --funding $FUND --pairs $P --funding-window 2026-09-17:2026-10-22 \$(cat $OUT/specs\$v.txt)
done
touch $OUT/D1V4_DONE
EOF
systemd-run --unit="$UNIT" --working-directory=$W -p Restart=on-failure -p RestartSec=$RESTART_S -p StartLimitIntervalSec=0 \
  --setenv=P40_L1=1 --setenv=P40_NOTRADE=through --setenv=P40_SHM_FRAC=0.9 \
  --setenv=P40_CORE=${CORE:-/root/p40core/bin/p40core-nopgo-g87097f2c} \
  bash "$OUT.run.sh"
echo "запущен $UNIT; готово = $OUT/D1V4_DONE; orch.csv — замеры"
