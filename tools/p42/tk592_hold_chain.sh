#!/usr/bin/env bash
# П-42 §13.18 (TK-592): цепочка отложенных суток на srv — кэш минуток (v20) → ворота кэша → движок v20 (только сигналы и дампы, b0 + b0p0) → ворота sigsx/levels против v19
# → таблица эпизодов суток ≥ 74 → признаки/метки/контроли (tk592_build). Метки и контроли в логах не печатаются, таблица не открывается до заморозки (§13.18 п. 12).
# Запуск: systemd-run --unit=tk592-hold -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle --collect bash /data/tk592/py/tk592_hold_chain.sh
# Готовность: /data/tk592/hold.rc (0 = успех); лог /data/tk592/hold.log.
set -uo pipefail
W=/data/tk592; BIN=$W/bin/alpha-p42-v20; PY=$W/py
export SIGFEAT_DAYS=80 SIGFEAT_CACHE=$W/cache SIGFEAT_TRADES=$W/aggtrades
cd "$W"
rm -f hold.rc
log() { echo "$(date -u +%T) $*" >> "$W/hold.log"; }
fail() { log "СТОП: $*"; echo 1 > "$W/hold.rc"; exit 1; }
: > "$W/hold.log"
bash $PY/tk592_hold_prep.sh >> "$W/hold.log" 2>&1 || fail prep
if [ "${HOLD_SKIP_ENGINE:-0}" != 1 ]; then
log "convert"
rm -rf "$W/cache"
$BIN breakout convert --src "$W/klines" --out "$W/cache" --threads 6 >> "$W/hold.log" 2>&1 || fail convert
log "ворота кэша"
python3 $PY/tk592_hold_gates.py cache /data/p42-t566/cache "$W/cache" "$W/gate_cache.json" >> "$W/hold.log" 2>&1 || fail "кэш не совпал с прежним"
log "движок v20"
printf 'b0 lat=4 latx=4\nb0p0 lat=4 latx=4 natr_min=0 t1h=0\n' > "$W/variants-hold.txt"
rm -rf "$W/out/eng"
env P42_SIGSX=14:79 P42_LEVELS=1 P42_SIGSX_ONLY=1 $BIN breakout tick --cache "$W/cache" --trades "$W/aggtrades" --variants "$W/variants-hold.txt" \
  --symbols "$(cat /data/p42-t566/symbols.txt)" --out "$W/out/eng" --threads 6 > "$W/eng.log" 2>&1 || fail "движок"
else
log "HOLD_SKIP_ENGINE=1: кэш и движок не пересчитываются (проверка хвоста цепочки)"
fi
log "ворота sigsx / levels против v19"
python3 $PY/tk592_hold_gates.py sigsx /data/p42-t588/out/eng "$W/out/eng" "$W/gate_sigsx.json" >> "$W/hold.log" 2>&1 || fail "sigsx/levels не совпали с v19"
log "эпизоды (сутки ≥ 74)"
rm -rf "$W/hold"; mkdir -p "$W/hold"
env SIGFEAT_MIN_DAY=74 python3 $PY/sigfeat_run.py "$W/out/eng" "$W/hold" --procs 6 > "$W/hold/run.log" 2>&1 || fail "sigfeat_run"
env SIGFEAT_MIN_DAY=74 python3 $PY/sigfeat_join.py "$W/out/eng" "$W/hold" > "$W/hold/join.log" 2>&1 || fail "sigfeat_join"
log "признаки и контроль (tk592_build)"
python3 $PY/tk592_build.py "$W/hold/episodes.parquet" "$W/hold_u0.parquet" --cache "$W/cache" --trades "$W/aggtrades" --min-day 74 --procs 6 > "$W/hold/build.log" 2>&1 || fail "tk592_build"
log "готово: таблица $W/hold_u0.parquet (не открывать до заморозки)"
echo 0 > "$W/hold.rc"
