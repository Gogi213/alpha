#!/usr/bin/env bash
# G-022 турбо: окно 3 мин на живой бирже рядом с боевым Python (В-141): Rust-коллектор в отдельном каталоге, те же 180 с меряем у обоих.
#   probe.sh <binance|bybit> <метка> <бинарь alpha> [доп. аргументы collect run]
# Результат — /data/turbo-probe/<метка>/: data/ (файлы Rust), health.json, winmon.json (CPU/Recv-Q/ожидание CPU по юнитам),
# accept-rust.json и accept-python.json (accept_ws.py на тех же 180 с), zstd-test.txt. Запускать фоновым юнитом (systemd-run), не ждать в ssh.
set -u
EX="$1"; TAG="$2"; BIN="$3"; shift 3
D=/data/turbo-probe/$TAG; mkdir -p "$D"
SRC=/data/turbo-probe/src/tools; PY=/root/bnws-venv/bin/python
case "$EX" in binance) PROD_UNIT=bnws-prod.service; PROD_ROOT=/data/bnws ;; bybit) PROD_UNIT=bbws-prod.service; PROD_ROOT=/data/bbws ;; *) echo "биржа?"; exit 2 ;; esac
UNIT="turbo-$TAG"
systemctl stop "$UNIT.service" 2>/dev/null
systemd-run --quiet --unit="$UNIT" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
  "$BIN" collect run --exchange "$EX" --root "$D/data" --health "$D/health.json" --lanes 2 --duration-s 290 --connect-budget 150 "$@"
sleep 70   # подключения, окно проверки пути 15 с, прогрев; окно меряется на установившемся режиме
T0=$(date +%s%3N)
"$PY" "$SRC/turbo/winmon.py" --seconds 180 --unit "$PROD_UNIT" --unit "$UNIT.service" --out "$D/winmon.json" > "$D/winmon.log" 2>&1
T1=$(date +%s%3N)
echo "$T0 $T1" > "$D/window-ms.txt"
cd "$SRC/collector" || exit 1
HEALTH="/data/health/$EX.json" "$PY" accept_ws.py --exchange "$EX" --root "$PROD_ROOT" --since-ms "$T0" --until-ms "$T1" --out "$D/accept-python.json" > "$D/accept-python.log" 2>&1
HEALTH="$D/health.json" "$PY" accept_ws.py --exchange "$EX" --root "$D/data" --since-ms "$T0" --until-ms "$T1" --out "$D/accept-rust.json" > "$D/accept-rust.log" 2>&1
# конец юнита: остановка по --duration-s — штатная, файлы закрыты
while systemctl is-active --quiet "$UNIT.service"; do sleep 2; done
cp "$D/health.json" "$D/health-final.json" 2>/dev/null
for f in "$D"/data/20*/*.tsv.zst; do
  echo "$(zstd -tq "$f" >/dev/null 2>&1 && echo ok || echo BAD) ok-file=$([ -f "$f.ok" ] && cat "$f.ok" | tr -d '\n' || echo нет) $(basename "$f")"
done > "$D/zstd-test.txt"
journalctl -u "$UNIT.service" --no-pager -n 30 > "$D/unit.log" 2>&1
echo done > "$D/DONE"
