#!/usr/bin/env bash
# П-42 §13.27 (TK-610): подгонка CatBoost (`tk610_fit.py fit`) на VPS София (4 ядра, venv `/root/tk610/venv`: python 3.12, catboost 1.2.10, numpy 2.2.4, pandas 2.2.3, pyarrow 26.0.1, scipy 1.15.3 — версии как у srv).
# Входы — малые таблицы признаков (parquet), aggTrades не нужны. Всё через ПК тарами; sha256 сверяется на обоих концах.
#   bash tools/p42/tk610_sofia.sh push <имя> <сторона L|S> <файл на srv …>   — код tools/p42 + train-*.parquet(+ .info.json) → /root/tk610/in/<имя>/
#   bash tools/p42/tk610_sofia.sh run  <имя> <сторона> <потоков> [--dry]      — юнит `tk610-fit-<имя>-<S>` (Nice 0), ВОЗВРАТ СРАЗУ; ждать: `CHECK_HOST=vps bash tools/srv-wait.sh file:/root/tk610/out/<имя>-<S>/DONE 115`
#   bash tools/p42/tk610_sofia.sh pull <имя> <сторона> <каталог на srv>       — fit-<S>.json + models/ → <каталог>; sha256 сверяется; DONE = код возврата
# Оценка (`score`) — на srv (секунды; нужны union-таблицы `/data/p42-tk599/ftab`). Остановка — только по имени своего юнита: `systemctl stop tk610-fit-…`.
set -euo pipefail
CMD="${1:?push|run|pull}"; NAME="${2:?имя}"; SIDE="${3:?сторона L|S}"; shift 3
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=30)
SRV=root@5.231.25.224; SOF=root@13.140.29.171; R=/root/tk610
sofia() { ssh "${K[@]}" "$SOF" "$@"; }
srv() { ssh "${K[@]}" "$SRV" "$@"; }
case "$SIDE" in L|S) ;; *) echo "сторона: L|S" >&2; exit 2 ;; esac
case "$CMD" in
push)
  [ $# -ge 1 ] || { echo "нужен хотя бы один train-….parquet на srv" >&2; exit 2; }
  FILES=("$@")
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  ( cd "$(dirname "$0")/../.." && tar cf - tools/p42/*.py ) | sofia "rm -rf $R/py-$NAME && mkdir -p $R/py-$NAME $R/in/$NAME $R/out && tar xf - -C $R/py-$NAME --strip-components=2"
  for f in "${FILES[@]}"; do
    b=$(basename "$f")
    srv "cd $(dirname "$f") && tar cf - $b \$(ls $b.info.json 2>/dev/null)" | sofia "tar xf - -C $R/in/$NAME"
    a=$(srv "sha256sum $f" | cut -d' ' -f1); c=$(sofia "sha256sum $R/in/$NAME/$b" | cut -d' ' -f1)
    [ "$a" = "$c" ] || { echo "sha256 расходится: $b srv=$a sofia=$c" >&2; exit 3; }
    echo "ok $b $a"
  done ;;
run)
  TH="${1:?потоков}"; shift; EXTRA="${*:-}"
  U="tk610-fit-$NAME-$SIDE"; OUT="$R/out/$NAME-$SIDE"
  sofia "systemctl reset-failed $U 2>/dev/null; rm -rf $OUT; mkdir -p $OUT; cat > $R/run-$NAME-$SIDE.sh <<EOS
#!/usr/bin/env bash
cd $R/py-$NAME
$R/venv/bin/python -W ignore tk610_fit.py fit --side $SIDE --train \$(ls $R/in/$NAME/train-*-$SIDE.parquet) --out $OUT --procs $TH $EXTRA > $OUT/fit.log 2>&1
rc=\\\$?
( cd $OUT && find fit-$SIDE.json models -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
echo \\\$rc > $OUT/DONE
EOS
systemd-run --unit=$U -p Nice=0 -p CPUQuota=$((TH*100))% --collect bash $R/run-$NAME-$SIDE.sh >/dev/null && echo started $U" ;;
pull)
  DEST="${1:?каталог на srv}"
  OUT="$R/out/$NAME-$SIDE"
  [ "$(sofia "cat $OUT/DONE 2>/dev/null")" = 0 ] || { echo "нет DONE=0 в $OUT" >&2; exit 4; }
  srv "mkdir -p $DEST"
  sofia "cd $OUT && tar cf - fit-$SIDE.json models SHA256SUMS fit.log" | srv "tar xf - -C $DEST"
  srv "cd $DEST && sha256sum -c SHA256SUMS | tail -n 5; sha256sum -c SHA256SUMS >/dev/null" && echo "pull ok → $DEST" ;;
*) echo "push|run|pull" >&2; exit 2 ;;
esac
