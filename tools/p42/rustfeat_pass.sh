#!/usr/bin/env bash
# TK-609 v6: полный проход `retitr_ftab build` с замером фаз и сверкой с эталоном TK-599 (ftab.csv байт в байт + eps 66 колонок побитово).
#  BIN=<alpha-feat> | py   TAG=<метка>  F12=1 (эпизоды — Rust `alpha-feat f12`, P42_F12_BIN=BIN) | auto (P42_F12_BIN не задана — умолчание v7: Rust от P42_FEAT_BIN) | 0 (по умолчанию — P42_F12_BIN=off, Python)  W=wa|wb|wc  VID=<поток>  SIDE=S|L  PROCS=N  CODE=<каталог кода>
set -uo pipefail
R=/data/p42-tk609; BIN=${BIN:?}; TAG=${TAG:?}; W=${W:-wa}; VID=${VID:-fa95205e801e5}; SIDE=${SIDE:-S}; CODE=${CODE:-$R/code-v6}
if [ "$SIDE" = S ]; then SX=short; else SX=long; fi
declare -A BD=([wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01)
D=$R/pass/$TAG-$W-$SIDE; rm -rf $D; mkdir -p $D
export TK594_METRICS=/data/p42-tk599/metrics-all TK594_TRADES=/data/p42-tk599/$W/aggtrades
unset P42_FEAT_BIN P42_F12_BIN
if [ "$BIN" != py ]; then export P42_FEAT_BIN=$BIN TK594_STORE=/data/p42-tk599/tick-store TK594_STORE_CAP_GB=0 TK594_STORE_MIN_FREE_GB=0; B5=$(md5sum $BIN | cut -c1-12); else B5=python; fi
# v7: без P42_F12_BIN эпизоды Rust сами, бинарём P42_FEAT_BIN (F12=auto — проверка этого умолчания); прогон «Python-эпизоды» просит off явно (F12=0)
case "${F12:-0}" in 1) export P42_F12_BIN=$BIN ;; auto) ;; *) export P42_F12_BIN=off ;; esac
echo "[$(date -u +%T)] $TAG $W $SIDE $VID feat=$B5 f12=$([ "${P42_F12_BIN:-}" = off ] && echo python || echo rust) procs=${PROCS:-4} load=$(cut -d' ' -f1-3 /proc/loadavg)"
python3 $R/py/tk609_pass_time.py $CODE -- build $SIDE --sigsx /data/p42-tk599/sx/$W/$VID/$SX-sigsx.csv --work $D --out $D/ftab.csv \
  --prod /data/tk594/py --cache /data/p42-tk599/$W/cache --trades /data/p42-tk599/$W/aggtrades --procs ${PROCS:-4} --base-date ${BD[$W]} --force > $D/run.log 2>&1
echo "rc=$?"; grep PHASES $D/run.log
cmp $D/ftab.csv /data/p42-tk599/ftab/$W/$VID/ftab.csv && echo "ftab.csv IDENTICAL"
if [ "${F12:-0}" != 0 ]; then
  python3 $R/code-v6/tools/p42/rustfeat_f12_gate.py compare /data/p42-tk599/ftab/$W/$VID/eps-$SIDE.parquet $D/eps-$SIDE.parquet.f12.csv --out $D/eps-gate.json 2>&1 | tail -2
fi
MODE=flow; [ "$SIDE" = L ] && MODE=deriv
python3 $R/py/rustfeat_gate.py compare /data/p42-tk599/ftab/$W/$VID/tk594-$MODE-$SIDE.parquet $D/tk594-$MODE-$SIDE.parquet --cols $MODE --out $D/ga1.json --eps $D/eps-$SIDE.parquet --base-date ${BD[$W]} 2>&1 | tail -1
echo "[$(date -u +%T)] load_end=$(cut -d' ' -f1-3 /proc/loadavg)"
