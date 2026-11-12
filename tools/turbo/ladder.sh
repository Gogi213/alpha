#!/usr/bin/env bash
# G-022 турбо: лестница темпа на ОДНО соединение — синтетический источник (`alpha collect synth`) против Rust-коллектора и Python-ядра.
#   ladder.sh <rust|python> <метка> <бинарь alpha> <темп> [<темп> …]     (темп — кадров/с на соединение; 64 символа, вид book)
# На ступень: 60 с работы; в /data/turbo-probe/<метка>/r<темп>/ — health, stats, итог ступени summary.json. Python — через py_baseline.py.
set -u
KIND="$1"; TAG="$2"; BIN="$3"; shift 3
D=/data/turbo-probe/$TAG; mkdir -p "$D"; SRC=/data/turbo-probe/src/tools; PY=/root/bnws-venv/bin/python
"$BIN" collect synth --listen 127.0.0.1:1 --symbols 64 --duration-s 1 --write-pool "$D/pool.json" >/dev/null 2>&1   # только пул
for R in "$@"; do
  S="$D/r$R"; rm -rf "$S"; mkdir -p "$S"; PORT=$((9100 + R % 800))
  systemd-run --quiet --unit="synth-$TAG-$R" --collect "$BIN" collect synth --listen 127.0.0.1:$PORT --symbols 64 --rate "$R" --duration-s 90 > /dev/null
  sleep 1
  if [ "$KIND" = rust ]; then
    systemd-run --quiet --unit="col-$TAG-$R" --collect -p KillMode=mixed "$BIN" collect run --exchange binance --plain-ws 127.0.0.1:$PORT --pool-file "$D/pool.json" --root "$S/data" --health "$S/health.json" --kinds book --streams-per-conn 64 --lanes 1 --duration-s 60
  else
    systemd-run --quiet --unit="col-$TAG-$R" --collect -p KillMode=mixed "$PY" "$SRC/turbo/py_baseline.py" --synth 127.0.0.1:$PORT --pool-file "$D/pool.json" --root "$S/data" --health "$S/health.json" --kinds book --streams-per-conn 100 --lanes 1
  fi
  sleep 25
  "$PY" "$SRC/turbo/winmon.py" --seconds 30 --unit "col-$TAG-$R.service" --unit "synth-$TAG-$R.service" --out "$S/winmon.json" > /dev/null 2>&1
  sleep 6
  systemctl stop "col-$TAG-$R.service" 2>/dev/null; sleep 3
  journalctl -u "synth-$TAG-$R.service" --no-pager -n 4 > "$S/synth-tail.txt" 2>&1
  systemctl stop "synth-$TAG-$R.service" 2>/dev/null
  echo "ступень $R: готово" >> "$D/progress.txt"
done
echo done > "$D/DONE"
