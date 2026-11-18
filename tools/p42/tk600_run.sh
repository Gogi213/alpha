#!/usr/bin/env bash
# П-42 §13.25 (TK-600): признаки X = f12 + FLOW16 + DERIV14 + TOX13 для строк таблиц TK-598 (eps-/feat- файлы `/data/p42-tk598/feat`).
#   bash tk600_run.sh feat F|aug|jul      — по каждому варианту окна: tox → reuse (если у TK-594 есть таблица окна) → tk594_feat для недостающих → assemble → G-R
#   bash tk600_run.sh dl                  — докачка июля: premiumIndexKlines 1m 30.06…17.07 и fundingRate 2026-06 (метрики 17.06…17.07 — докачка TK-599, читается после её `done`)
# Запуск на srv под systemd-run (Nice=19): systemd-run --unit=tk600-feat-F -p Nice=19 --collect bash tk600_run.sh feat F
# Готовность окна — файл $W/feat/DONE-<окно> (код возврата внутри).
set -uo pipefail
W=/data/p42-tk600; C=$W/py; FE=/data/p42-tk598/feat; PY=/data/tk592/venv/bin/python; PROCS="${PROCS:-4}"
mkdir -p "$W/feat" "$W/log" "$W/tmp"
cd "$C" || exit 1
cmd="${1:?feat|dl}"

if [ "$cmd" = dl ]; then
  KIND=premium OUT=$W/premium LOGDIR=$W/log-dl PAR="${PAR:-6}" bash "$C/dl_metrics.sh" 2026-06-30 2026-07-17 "2026-06"
  exit $?
fi

WIN="${2:?F|aug|jul}"
case "$WIN" in
  F)   RUN=F-1;   T0=2026-09-01; TR=/data/aggtrades/binance; VARS="L_u1 S_u1 L_u0 S_u0"
       T594_EPS=/data/tk594/ep_u0.parquet; T594_FLOW=/data/tk594/feat_flow.parquet; T594_DERIV=/data/tk594/feat_deriv.parquet; T594_T0=2026-09-01
       MET=/data/p42-metrics; PRE=/data/p42-premium; FUN=/data/p42-metrics/funding ;;
  aug) RUN=aug-1; T0=2026-07-18; TR=/data/p42-tk596/aug/aggtrades; VARS="L_u1 S_u1"
       T594_EPS=/data/tk594/aug_u0.parquet; T594_FLOW=/data/tk594/aug_feat_flow.parquet; T594_DERIV=/data/tk594/aug_feat_deriv.parquet; T594_T0=2026-07-18
       MET=/data/p42-metrics; PRE=/data/p42-premium; FUN=/data/p42-metrics/funding ;;
  jul) RUN=jul-1; T0=2026-06-17; TR=/data/p42-tk596/jul/aggtrades; VARS="L_u1 S_u1"
       T594_EPS=; T594_FLOW=; T594_DERIV=; T594_T0=
       MET=$W/metrics-all; PRE=$W/premium-all; FUN=$W/fund-all ;;
  *) echo "окно: F|aug|jul" >&2; exit 2 ;;
esac
export TK594_TRADES=$TR TK594_METRICS=$MET TK594_PREMIUM=$PRE TK594_FUNDING_M=$FUN
rc_all=0
fail() { echo "FAIL $*" | tee -a "$W/log/feat-$WIN.log"; echo 1 > "$W/feat/DONE-$WIN"; exit 1; }
log() { echo "$(date -u +%T) $*" | tee -a "$W/log/feat-$WIN.log"; }

if [ "$WIN" = jul ]; then
  # метрики июня–июля (TK-599) + премия / фандинг июня (tk600-dl): ждём их завершения, затем собираем общие каталоги ссылок
  until [ -e /data/p42-tk599/metrics-jun-jul/log/done ] && [ -e "$W/log-dl/done" ]; do sleep 30; done
  log "входы июля готовы; сборка каталогов ссылок"
  $PY - <<'PYEOF' || fail merge-dirs
import os, glob
W = "/data/p42-tk600"
def merge(dst, srcs, sub=""):
    n = 0
    for src in srcs:
        for sd in sorted(glob.glob(os.path.join(src, sub, "*"))):
            if not os.path.isdir(sd) or os.path.basename(sd) in ("log", "funding"):
                continue
            sym = os.path.basename(sd)
            os.makedirs(os.path.join(dst, sym), exist_ok=True)
            for f in os.listdir(sd):
                if f.endswith(".zip"):
                    d = os.path.join(dst, sym, f)
                    if not os.path.lexists(d):
                        os.symlink(os.path.realpath(os.path.join(sd, f)), d); n += 1
    return n
print("metrics-all", merge(W + "/metrics-all", ["/data/p42-tk599/metrics-jun-jul", "/data/p42-metrics"]))
print("premium-all", merge(W + "/premium-all", [W + "/premium", "/data/p42-premium"]))
print("fund-all", merge(W + "/fund-all", [W + "/premium/funding", "/data/p42-metrics/funding"]))
PYEOF
fi

for V in $VARS; do
  EPS=$FE/eps-$RUN-$V.parquet; F12=$FE/feat-$RUN-$V.parquet; T=$W/tmp/$WIN-$V
  [ -s "$EPS" ] && [ -s "$F12" ] || fail "нет входа $V"
  log "== $WIN $V: tox"
  $PY tk600_feat.py tox "$EPS" "$T-tox.parquet" --base-date $T0 --procs "$PROCS" >> "$W/log/feat-$WIN.log" 2>&1 || fail "tox $V"
  if [ -n "$T594_EPS" ]; then
    $PY tk600_feat.py reuse "$EPS" --base-date $T0 --tk594-base-date $T594_T0 --tk594-eps $T594_EPS --tk594-flow $T594_FLOW --tk594-deriv $T594_DERIV \
      --have "$T-have.parquet" --need "$T-need.parquet" >> "$W/log/feat-$WIN.log" 2>&1 || fail "reuse $V"
    if [ "$V" = L_u0 ]; then   # G-R: 150 пересекающихся строк пересчитываются tk594_feat (один вариант на окно, самый большой)
      $PY tk600_feat.py gate-reuse --eps "$EPS" --have "$T-have.parquet" --base-date $T0 --n 150 > "$W/feat/gate-R-$WIN.json" 2>> "$W/log/feat-$WIN.log" || fail "G-R $WIN"
    fi
  else
    $PY - "$EPS" "$T-have.parquet" "$T-need.parquet" <<'PYEOF' || fail "need $V"
import sys, pandas as pd
e = pd.read_parquet(sys.argv[1], columns=["ep_id", "sym", "side", "sig_ms", "day", "turn24h"])
pd.DataFrame({"ep_id": []}).to_parquet(sys.argv[2]); e.to_parquet(sys.argv[3])
PYEOF
  fi
  NEED=$($PY -c "import pandas as pd,sys; print(len(pd.read_parquet(sys.argv[1])))" "$T-need.parquet")
  COMP=
  if [ "$NEED" -gt 0 ]; then
    log "== $WIN $V: tk594_feat для $NEED строк"
    $PY tk594_feat.py "$T-need.parquet" "$T-comp.parquet" --mode all --base-date $T0 --procs "$PROCS" >> "$W/log/feat-$WIN.log" 2>&1 || fail "tk594 $V"
    COMP="--computed $T-comp.parquet"
  fi
  $PY tk600_feat.py assemble --f12 "$F12" --eps "$EPS" --have "$T-have.parquet" $COMP --tox "$T-tox.parquet" --out "$W/feat/feat-X-$RUN-$V.parquet" >> "$W/log/feat-$WIN.log" 2>&1 || fail "assemble $V"
  log "== $WIN $V: готово"
done
echo 0 > "$W/feat/DONE-$WIN"
log "ОКНО $WIN ГОТОВО"
